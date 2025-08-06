#!/usr/bin/env python3
import os

import aws_cdk as cdk

from py_api.py_api_stack import PyApiStack


app = cdk.App()

py_stack = PyApiStack(app, "PyApiStack", stackVars={
    "region": "us-east-1", 
    "prefix": "rag-uno"
})

app.synth()
