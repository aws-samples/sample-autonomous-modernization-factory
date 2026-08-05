# =============================================================================
# Module: VPC - Public/Private subnets across AZs with NAT egress
# Public subnets  -> Internet Gateway (for the ALB)
# Private subnets -> NAT Gateway     (for Fargate tasks)
# =============================================================================

locals {
  name = "${var.project_name}-${var.environment}"

  # Number of NAT gateways to create: one per AZ, or a single shared one.
  nat_count = var.enable_nat_gateway ? (var.single_nat_gateway ? 1 : length(var.availability_zones)) : 0

  common_tags = merge(
    {
      Component = "vpc"
      ManagedBy = "terraform"
    },
    var.tags,
  )
}

# --- VPC ---
resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, {
    Name = "${local.name}-vpc"
  })
}

# --- Internet Gateway (public egress) ---
resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.common_tags, {
    Name = "${local.name}-igw"
  })
}

# --- Public Subnets ---
resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.this.id
  cidr_block        = var.public_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]
  # The internet-facing ALB and NAT gateways get public IPs from their own
  # config/EIPs, so the subnet does not need to auto-assign public IPs.
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Name = "${local.name}-public-${var.availability_zones[count.index]}"
    Tier = "public"
  })
}

# --- Private Subnets ---
resource "aws_subnet" "private" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.private_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Name = "${local.name}-private-${var.availability_zones[count.index]}"
    Tier = "private"
  })
}

# --- NAT Gateways (+ EIPs) in public subnets ---
resource "aws_eip" "nat" {
  count  = local.nat_count
  domain = "vpc"

  tags = merge(local.common_tags, {
    Name = "${local.name}-nat-eip-${count.index}"
  })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_nat_gateway" "this" {
  count = local.nat_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(local.common_tags, {
    Name = "${local.name}-nat-${count.index}"
  })

  depends_on = [aws_internet_gateway.this]
}

# --- Public Route Table (0.0.0.0/0 -> IGW) ---
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.common_tags, {
    Name = "${local.name}-public-rt"
    Tier = "public"
  })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- Private Route Tables (0.0.0.0/0 -> NAT) ---
# One route table per AZ so each private subnet uses its AZ-local NAT.
# When single_nat_gateway = true, all private subnets share the single NAT.
resource "aws_route_table" "private" {
  count = length(var.availability_zones)

  vpc_id = aws_vpc.this.id

  tags = merge(local.common_tags, {
    Name = "${local.name}-private-rt-${var.availability_zones[count.index]}"
    Tier = "private"
  })
}

resource "aws_route" "private_nat" {
  count = var.enable_nat_gateway ? length(var.availability_zones) : 0

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = var.single_nat_gateway ? aws_nat_gateway.this[0].id : aws_nat_gateway.this[count.index].id
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}
