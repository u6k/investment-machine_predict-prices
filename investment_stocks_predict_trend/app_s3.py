import os
import io
import joblib
import pandas as pd
import boto3


def get_client():
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["AWS_S3_SECRET_KEY"],
        endpoint_url=os.environ["AWS_S3_ENDPOINT_URL"]
    )

    return s3


def read_dataframe(s3_bucket, s3_key, **kwargs):
    s3 = get_client()
    obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    df = pd.read_csv(obj["Body"], **kwargs)

    return df


def write_dataframe(df, s3_bucket, s3_key):
    with io.StringIO() as buf:
        df.to_csv(buf)
        s3 = get_client()
        s3.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=io.BytesIO(buf.getvalue().encode())
        )


def write_sklearn_model(clf, s3_bucket, s3_key):
    with io.BytesIO() as buf:
        joblib.dump(clf, buf, compress=9)
        s3 = get_client()
        s3.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=io.BytesIO(buf.getvalue())
        )


def read_sklearn_model(s3_bucket, s3_key):
    s3 = get_client()
    obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    with io.BytesIO(obj["Body"].read()) as buf:
        clf = joblib.load(buf)

    return clf
