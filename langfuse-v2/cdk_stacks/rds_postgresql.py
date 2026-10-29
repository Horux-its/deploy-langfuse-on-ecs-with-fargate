#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# vim: tabstop=2 shiftwidth=2 softtabstop=2 expandtab

import json

import aws_cdk as cdk

from aws_cdk import (
  Stack,
  aws_ec2,
  aws_logs,
  aws_rds,
  aws_secretsmanager
)

from constructs import Construct


class RdsPostgresqlStack(Stack):

  def __init__(self, scope: Construct, construct_id: str, vpc, **kwargs) -> None:
    super().__init__(scope, construct_id, **kwargs)

    db_instance_identifier = self.node.try_get_context('db_instance_identifier') or 'langfuse-db'

    sg_postgresql_client = aws_ec2.SecurityGroup(self, 'PostgreSQLClientSG',
      vpc=vpc,
      allow_all_outbound=True,
      description='security group for postgresql client',
      security_group_name=f'{db_instance_identifier}-postgresql-client-sg'
    )
    cdk.Tags.of(sg_postgresql_client).add('Name', 'postgresql-client-sg')

    sg_postgresql_server = aws_ec2.SecurityGroup(self, 'PostgreSQLServerSG',
      vpc=vpc,
      allow_all_outbound=True,
      description='security group for postgresql',
      security_group_name=f'{db_instance_identifier}-postgresql-server-sg'
    )
    sg_postgresql_server.add_ingress_rule(peer=sg_postgresql_client, connection=aws_ec2.Port.tcp(5432),
      description='postgresql-client-sg')
    cdk.Tags.of(sg_postgresql_server).add('Name', 'postgresql-server-sg')

    rds_subnet_group = aws_rds.SubnetGroup(self, 'PostgreSQLSubnetGroup',
      description='subnet group for postgresql',
      subnet_group_name=f'postgresql-{self.stack_name}',
      vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS),
      vpc=vpc
    )

    #XXX: In order to exclude punctuations when generating a password
    # use aws_secretsmanager.Secret instead of aws_rds.DatabaseSecret.
    # Othwerise, an error occurred such as:
    # "All characters of the desired type have been excluded"
    db_secret = aws_secretsmanager.Secret(self, 'DatabaseSecret',
      generate_secret_string=aws_secretsmanager.SecretStringGenerator(
        secret_string_template=json.dumps({"username": "postgres"}),
        generate_string_key="password",
        exclude_punctuation=True,
        password_length=8
      )
    )
    rds_credentials = aws_rds.Credentials.from_secret(db_secret)

    rds_engine = aws_rds.DatabaseInstanceEngine.postgres(version=aws_rds.PostgresEngineVersion.VER_15_4)

    #XXX: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithParamGroups.html
    rds_param_group = aws_rds.ParameterGroup(self, 'PostgreSQLParamGroup',
      engine=rds_engine,
      description='Custom parameter group for postgresql15',
      parameters={
        'log_min_duration_statement': '15000', # 15 sec
        'default_transaction_isolation': 'read committed',
        'client_encoding': 'UTF8'
      }
    )

    #XXX: RDS PostgreSQL (non-Aurora) single-AZ instance: no writer/reader split,
    # no distributed storage. HA (Multi-AZ) and storage size are deliberately
    # kept minimal here for a low-cost/POC deployment.
    db_instance = aws_rds.DatabaseInstance(self, 'Database',
      engine=rds_engine,
      credentials=rds_credentials, # A username of 'admin' (or 'postgres' for PostgreSQL) and SecretsManager-generated password
      instance_type=aws_ec2.InstanceType.of(aws_ec2.InstanceClass.BURSTABLE4_GRAVITON, aws_ec2.InstanceSize.MEDIUM),
      parameter_group=rds_param_group,
      auto_minor_version_upgrade=False,
      multi_az=False,
      allocated_storage=20,
      storage_type=aws_rds.StorageType.GP3,
      cloudwatch_logs_retention=aws_logs.RetentionDays.THREE_DAYS,
      instance_identifier=db_instance_identifier,
      subnet_group=rds_subnet_group,
      backup_retention=cdk.Duration.days(3),
      preferred_backup_window="03:00-04:00",
      security_groups=[sg_postgresql_server],
      vpc=vpc,
      vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS)
    )

    self.sg_rds_client = sg_postgresql_client
    self.database_secret = db_instance.secret
    self.database = db_instance


    cdk.CfnOutput(self, 'DBInstanceEndpoint',
      value=db_instance.db_instance_endpoint_address,
      export_name=f'{self.stack_name}-DBInstanceEndpoint')
    cdk.CfnOutput(self, 'RDSClientSecurityGroupId',
      value=sg_postgresql_client.security_group_id,
      export_name=f'{self.stack_name}-RDSClientSecurityGroupId')
    cdk.CfnOutput(self, 'DBSecretName',
      value=db_instance.secret.secret_name,
      export_name=f'{self.stack_name}-DBSecretName')
