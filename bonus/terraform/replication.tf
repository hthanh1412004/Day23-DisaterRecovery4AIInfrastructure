terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}
provider "aws" {
  alias  = "replica"
  region = "us-west-2"
}
variable "bucket_prefix" {
  type        = string
  description = "Globally unique prefix, supplied before an actual deployment."
}
resource "aws_s3_bucket" "primary" {
  bucket = "${var.bucket_prefix}-primary"
}
resource "aws_s3_bucket" "replica" {
  provider = aws.replica
  bucket   = "${var.bucket_prefix}-replica"
}
resource "aws_s3_bucket_versioning" "primary" {
  bucket = aws_s3_bucket.primary.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_versioning" "replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.replica.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_iam_role" "replication" {
  name_prefix = "lab23-replication-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "s3.amazonaws.com" }, Action = "sts:AssumeRole"
    }]
  })
}
resource "aws_iam_role_policy" "replication" {
  role = aws_iam_role.replication.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetReplicationConfiguration", "s3:ListBucket"], Resource = aws_s3_bucket.primary.arn },
      { Effect = "Allow", Action = ["s3:GetObjectVersionForReplication", "s3:GetObjectVersionAcl", "s3:GetObjectVersionTagging"], Resource = "${aws_s3_bucket.primary.arn}/*" },
      { Effect = "Allow", Action = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"], Resource = "${aws_s3_bucket.replica.arn}/*" }
    ]
  })
}
resource "aws_s3_bucket_replication_configuration" "artifacts" {
  depends_on = [aws_s3_bucket_versioning.primary, aws_s3_bucket_versioning.replica, aws_iam_role_policy.replication]
  role       = aws_iam_role.replication.arn
  bucket     = aws_s3_bucket.primary.id
  rule {
    id       = "vectors-weights-manifest"
    priority = 1
    status   = "Enabled"
    filter { prefix = "" }
    delete_marker_replication { status = "Disabled" }
    destination {
      bucket        = aws_s3_bucket.replica.arn
      storage_class = "STANDARD"
    }
  }
}
