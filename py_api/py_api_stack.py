import json

from aws_cdk import (
    Duration,
    Stack,
    RemovalPolicy,
    aws_lambda,
    aws_apigateway,
    aws_s3,
    aws_iam,
    aws_opensearchserverless
)

from constructs import Construct

class PyApiStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, stackVars: dict,**kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3
        documents_bucket = aws_s3.Bucket(
            self, f"{stackVars['prefix']}-DocumentsBucket",
            bucket_name=f"{stackVars['prefix'].lower()}-rag-documents-{self.account}",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            cors=[aws_s3.CorsRule(
                allowed_methods=[aws_s3.HttpMethods.GET, aws_s3.HttpMethods.PUT, aws_s3.HttpMethods.POST],
                allowed_origins=["*"],
                allowed_headers=["*"]
            )]
        )

        # OpenSearch

        # Encryption Policy (KMS)
        encryption_policy = aws_opensearchserverless.CfnSecurityPolicy(
            self, f"{stackVars['prefix']}-EncryptionPolicy",
            name=f"{stackVars['prefix'].lower()}-encryption-policy",
            type="encryption",
            policy=json.dumps({
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{stackVars['prefix'].lower()}-rag-collection"]
                    }
                ],
                "AWSOwnedKey": True
            })
        )   

        # Network Policy (Public access for demo)
        network_policy = aws_opensearchserverless.CfnSecurityPolicy(
            self, f"{stackVars['prefix']}-NetworkPolicy",
            name=f"{stackVars['prefix'].lower()}-network-policy",
            type="network",
            policy=json.dumps([
                {
                    "Description": "Public access for RAG collection",
                    "Rules": [
                        {
                            "ResourceType": "dashboard",
                            "Resource": [f"collection/{stackVars['prefix'].lower()}-rag-collection"]
                        },
                        {
                            "ResourceType": "collection",
                            "Resource": [f"collection/{stackVars['prefix'].lower()}-rag-collection"]
                        }
                    ],
                    "AllowFromPublic": True
                }
            ])
        )

        # OpenSearch Serverless Collection
        opensearch_collection = aws_opensearchserverless.CfnCollection(
            self, f"{stackVars['prefix']}-Collection",
            name=f"{stackVars['prefix'].lower()}-rag-collection",
            type="VECTORSEARCH",
            description="RAG Multi-tenant Vector Collection"
        )

        # Dependencies
        opensearch_collection.add_dependency(encryption_policy)
        opensearch_collection.add_dependency(network_policy)   

        #===================================================================
        
        # ==================================================================
        # Bedrock Execution Role (for Knowledge Bases)
        bedrock_kb_role = aws_iam.Role(
            self, f"{stackVars['prefix']}-BedrockKBRole",
            role_name=f"{stackVars['prefix']}-BedrockKBExecutionRole",
            assumed_by=aws_iam.ServicePrincipal("bedrock.amazonaws.com"),
            inline_policies={
                "BedrockKBPolicy": aws_iam.PolicyDocument(
                    statements=[
                        # S3 permissions
                        aws_iam.PolicyStatement(
                            effect=aws_iam.Effect.ALLOW,
                            actions=["s3:GetObject", "s3:ListBucket"],
                            resources=[
                                documents_bucket.bucket_arn,
                                f"{documents_bucket.bucket_arn}/*"
                            ]
                        ),
                        # OpenSearch permissions
                        aws_iam.PolicyStatement(
                            effect=aws_iam.Effect.ALLOW,
                            actions=["aoss:APIAccessAll"],
                            resources=[opensearch_collection.attr_arn]
                        ),
                        # Bedrock permissions
                        aws_iam.PolicyStatement(
                            effect=aws_iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream"
                            ],
                            resources=["*"]
                        )
                    ]
                )
            }
        )

        # Lambda Execution Role
        lambda_role = aws_iam.Role(
            self, f"{stackVars['prefix']}-LambdaRole",
            assumed_by=aws_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                aws_iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "BedrockAccessPolicy": aws_iam.PolicyDocument(
                    statements=[
                        aws_iam.PolicyStatement(
                            effect=aws_iam.Effect.ALLOW,
                            actions=[
                                "bedrock-agent:*",
                                "bedrock-agent-runtime:*",
                                "bedrock:*"
                            ],
                            resources=["*"]
                        ),
                        aws_iam.PolicyStatement(
                            effect=aws_iam.Effect.ALLOW,
                            actions=["iam:PassRole"],
                            resources=[bedrock_kb_role.role_arn]
                        ),
                        aws_iam.PolicyStatement(
                            effect=aws_iam.Effect.ALLOW,
                            actions=[
                                "s3:GetObject",
                                "s3:PutObject",           
                                "s3:DeleteObject",
                                "s3:ListBucket",
                                "s3:GetBucketLocation"
                            ],
                            resources=[
                                documents_bucket.bucket_arn,        
                                f"{documents_bucket.bucket_arn}/*"
                                ]
                        ),
                        aws_iam.PolicyStatement(
                            effect=aws_iam.Effect.ALLOW,
                            actions=[
                                "aoss:CreateIndex",
                                "aoss:DeleteIndex", 
                                "aoss:UpdateIndex",
                                "aoss:DescribeIndex",
                                "aoss:ReadDocument",
                                "aoss:WriteDocument",
                                "aoss:CreateCollection",
                                "es:ESHttpGet",
                                "es:ESHttpPost",
                                "es:ESHttpPut"
                            ],
                            resources=[opensearch_collection.attr_arn, f"{opensearch_collection.attr_arn}/*"]
                        )
                    ]
                )
            }
        )

        # Data Access Policy for OpenSearch
        base_data_access_policy = aws_opensearchserverless.CfnAccessPolicy(
            self, f"{stackVars['prefix']}-BaseDataAccessPolicy",
            name=f"{stackVars['prefix'].lower()}-base-data-access",
            type="data",
            policy=json.dumps([
                {
                    "Rules": [
                        {
                            "ResourceType": "collection",
                            "Resource": [f"collection/{stackVars['prefix'].lower()}-rag-collection"],
                            "Permission": ["aoss:*"]
                        },
                        {
                            "ResourceType": "index",
                            "Resource": [f"index/{stackVars['prefix'].lower()}-rag-collection/*"],
                            "Permission": ["aoss:*"]
                        }
                    ],
                    "Principal": [
                        bedrock_kb_role.role_arn,
                        lambda_role.role_arn
                    ]
                }
            ])
        ) 
        
        #===================================================================
        #===================================================================

        # vars
        common_env = {
            "OPENSEARCH_COLLECTION_ENDPOINT": opensearch_collection.attr_collection_endpoint,
            "OPENSEARCH_COLLECTION_ARN": opensearch_collection.attr_arn,
            "S3_BUCKET_NAME": documents_bucket.bucket_name,
            "S3_BUCKET_ARN": documents_bucket.bucket_arn,
            "BEDROCK_KB_ROLE_ARN": bedrock_kb_role.role_arn,
            "REGION": self.region
        }

        # Lambda Functions
        hello_function = aws_lambda.Function(self, f"{stackVars['prefix']}-HelloFunction",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="hello.lambda_handler",
            code=aws_lambda.Code.from_asset("functions"),
            timeout=Duration.minutes(2),
            memory_size=512,
            environment=common_env,
            role=lambda_role
        )

        create_function = aws_lambda.Function(self, f"{stackVars['prefix']}-CreateFunction",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="create.lambda_handler",
            code=aws_lambda.Code.from_asset("functions"),
            timeout=Duration.minutes(5),
            memory_size=512,
            environment=common_env,
            role=lambda_role
        )

        list_function = aws_lambda.Function(self, f"{stackVars['prefix']}-ListFunction",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="list_kb.lambda_handler",
            code=aws_lambda.Code.from_asset("functions"),
            timeout=Duration.seconds(30),
            memory_size=128,
            environment=common_env,
            role=lambda_role
        )

        # API Gateway
        api = aws_apigateway.RestApi(self, f"{stackVars['prefix']}-Python-API_RAG",
            description="This is a Hello World API"
        )

        # API Gateway Resources
        api.root.add_resource("hello").add_method("POST", aws_apigateway.LambdaIntegration(hello_function))
        api.root.add_resource("create").add_method("POST", aws_apigateway.LambdaIntegration(create_function))
        api.root.add_resource("list").add_method("GET", aws_apigateway.LambdaIntegration(list_function))
        

        
