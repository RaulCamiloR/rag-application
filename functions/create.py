import json
import boto3
import os
from datetime import datetime
from botocore.exceptions import ClientError

def lambda_handler(event, context):

    headers= {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization'
        }

    try:
        body = json.loads(event['body'])
        client_id = body['client_id'].lower().strip()  
        
        if not client_id or len(client_id) < 2:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({
                    'error': 'client_id must be at least 2 characters long'
                })
            }
        
        if not client_id.replace('-', '').replace('_', '').isalnum():
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({
                    'error': 'client_id can only contain letters, numbers, hyphens and underscores'
                })
            }
        

        opensearch_collection_arn = os.environ['OPENSEARCH_COLLECTION_ARN']
        opensearch_collection_endpoint = os.environ['OPENSEARCH_COLLECTION_ENDPOINT']
        s3_bucket_name = os.environ['S3_BUCKET_NAME']
        s3_bucket_arn = os.environ['S3_BUCKET_ARN']
        bedrock_kb_role_arn = os.environ['BEDROCK_KB_ROLE_ARN']
        region = os.environ['REGION']
        
        print(f"Creating Knowledge Base for client: {client_id}")
        print(f"Region: {region}")
        print(f"S3 Bucket: {s3_bucket_name}")
        

        bedrock_client = boto3.client('bedrock-agent', region_name=region)
        s3_client = boto3.client('s3', region_name=region)
        
        from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
        

        session = boto3.Session()
        credentials = session.get_credentials()
        auth = AWSV4SignerAuth(credentials, region)
        
        host = opensearch_collection_endpoint.replace('https://', '').replace('http://', '')
        
        opensearch_client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=60
        )
        
        try:
            existing_kbs = bedrock_client.list_knowledge_bases()
            for kb in existing_kbs['knowledgeBaseSummaries']:
                if kb['name'] == f"kb-{client_id}":
                    return {
                        'statusCode': 409, 
                        'headers': headers,
                        'body': json.dumps({
                            'error': f'Knowledge Base for client "{client_id}" already exists',
                            'existing_kb_id': kb['knowledgeBaseId']
                        })
                    }
        except ClientError as e:
            print(f"Warning checking existing KBs: {e}")
        
        folder_key = f"{client_id}/"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        readme_content = f"""# Knowledge Base para {client_id}"""
        
        try:
            s3_client.put_object(
                Bucket=s3_bucket_name,
                Key=f"{folder_key}README.md",
                Body=readme_content.encode('utf-8'),
                ContentType='text/markdown'
            )
            print(f"Carpeta creada: s3://{s3_bucket_name}/{folder_key}")
        except ClientError as e:
            print(f"Error creando carpeta S3: {e}")
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({'error': f'Failed to create S3 folder: {str(e)}'})
            }
        

        index_name = f"{client_id}_index"
        try:
            print(f"Creating OpenSearch index: {index_name}")
            
            index_body = {
                "settings": {
                    "index.knn": True,
                    "number_of_shards": 1,
                    "number_of_replicas": 0
                },
                "mappings": {
                    "properties": {
                        "vector": {
                            "type": "knn_vector",
                            "dimension": 1536,  
                            "method": {
                                "name": "hnsw",
                                "engine": "faiss",
                                "parameters": {
                                    "ef_construction": 512,
                                    "m": 16
                                }
                            }
                        },
                        "text": {
                            "type": "text"
                        },
                        "metadata": {
                            "type": "text"
                        }
                    }
                }
            }
            
            if opensearch_client.indices.exists(index=index_name):
                print(f"Index {index_name} already exists, skipping creation")
            else:
                response = opensearch_client.indices.create(
                    index=index_name,
                    body=index_body
                )
                print(f"OpenSearch index created: {index_name}")
                print(f"Index response: {response}")
            
        except Exception as e:
            print(f"Error creating OpenSearch index: {e}")
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({'error': f'Failed to create OpenSearch index: {str(e)}'})
            }

        kb_name = f"kb-{client_id}"
        try:
            print(f"Creating Knowledge Base: {kb_name}")
            kb_response = bedrock_client.create_knowledge_base(
                name=kb_name,
                description=f"Knowledge Base para cliente {client_id} - RAG Multi-tenant Demo",
                roleArn=bedrock_kb_role_arn,
                knowledgeBaseConfiguration={
                    'type': 'VECTOR',
                    'vectorKnowledgeBaseConfiguration': {
                        'embeddingModelArn': 'arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1'
                    }
                },
                storageConfiguration={
                    'type': 'OPENSEARCH_SERVERLESS',
                    'opensearchServerlessConfiguration': {
                        'collectionArn': opensearch_collection_arn,
                        'vectorIndexName': f"{client_id}_index",
                        'fieldMapping': {
                            'vectorField': 'vector',
                            'textField': 'text', 
                            'metadataField': 'metadata'
                        }
                    }
                }
            )
            
            kb_id = kb_response['knowledgeBase']['knowledgeBaseId']
            print(f"Knowledge Base creada: {kb_id}")
            
        except ClientError as e:
            print(f"Error creando Knowledge Base: {e}")
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({'error': f'Failed to create Knowledge Base: {str(e)}'})
            }
        
        try:
            print(f"Creating Data Source for S3 prefix: {folder_key}")
            ds_response = bedrock_client.create_data_source(
                knowledgeBaseId=kb_id,
                name=f"s3-datasource-{client_id}",
                description=f"S3 data source para {client_id} - carpeta {folder_key}",
                dataSourceConfiguration={
                    'type': 'S3',
                    's3Configuration': {
                        'bucketArn': s3_bucket_arn,
                        'inclusionPrefixes': [folder_key]  
                    }
                }
            )
            
            data_source_id = ds_response['dataSource']['dataSourceId']
            print(f"Data Source creada: {data_source_id}")
            
        except ClientError as e:
            print(f"Error creando Data Source: {e}")
            try:
                print(f"Cleaning up Knowledge Base: {kb_id}")
                bedrock_client.delete_knowledge_base(knowledgeBaseId=kb_id)
            except Exception as cleanup_error:
                print(f"Failed to cleanup KB: {cleanup_error}")
                
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({'error': f'Failed to create Data Source: {str(e)}'})
            }
        

        s3_console_url = f"https://s3.console.aws.amazon.com/s3/buckets/{s3_bucket_name}?region={region}&prefix={folder_key}"
        
        success_response = {
            'success': True,
            'message': f'Knowledge Base successfully created for client {client_id}',
            'kb_id': kb_id,
            'data_source_id': data_source_id,
            'client_id': client_id,
            's3_bucket': s3_bucket_name,
            's3_folder': folder_key,
            's3_console_url': s3_console_url,
            'opensearch_index': f"{client_id}_index",
            'status': 'created',
            'timestamp': timestamp,
            'next_steps': [
                f"1. Upload documents to: s3://{s3_bucket_name}/{folder_key}",
                f"2. Or use AWS Console: {s3_console_url}",
                "3. Use POST /admin/sync-kb with kb_id to process documents", 
                "4. Query your knowledge base with POST /query"
            ]
        }
        
        print("Knowledge Base creation completed successfully!")
        print(f"Response: {json.dumps(success_response, indent=2)}")
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps(success_response)
        }
        
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return {
            'statusCode': 400,
            'headers': headers,
            'body': json.dumps({'error': 'Invalid JSON in request body'})
        }
        
    except KeyError as e:
        print(f"Missing required field: {e}")
        return {
            'statusCode': 400,
            'headers': headers,
            'body': json.dumps({'error': f'Missing required field: {str(e)}'})
        }
        
    except Exception as e:
        print(f"Error: {e}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }