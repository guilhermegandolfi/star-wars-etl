from pyspark.sql.functions import *
from delta.tables import *
from pyspark.sql import SparkSession
import json
from datetime import datetime
import boto3


class IngestionBroze:

    def __init__(self, bucket, table, idt_key=None, partition=None):
        self.bucket = bucket
        self.table = table
        self.idt_key = idt_key
        self.partition = partition

    def spark_session(self):

        spark = SparkSession.builder.appName("Lakehouse") \
            .config("spark.jars.packages", "io.delta:delta-core_2.12:1.0.0") \
            .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
            .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog") \
            .config("spark.databricks.delta.schema.autoMerge.enabled", "true") \
            .config("spark.databricks.delta.autoOptimize.optimizeWrite", "true") \
            .config("spark.databricks.delta.optimizeWrite.enabled", "true") \
            .config("spark.databricks.delta.vacuum.parallelDelete.enabled", "true") \
            .getOrCreate()

        return spark

    def load_table(self, spark):
        with open(f'schemas/{self.table}.json', 'r') as f:
            data = json.load(f)
            schema = StructType.fromJson(data)

        df = spark.read.option("multiline", "true").json(
            f"s3://{self.bucket}/raw_data/{self.table}", schema=schema)

        return df

    def transform_table(self, df):

        dat_load = datetime.now()
        dat_load = dat_load.strftime('%Y-%m-%d %H:%M:%S')

        df = df.select(explode(col('results')).alias('results'))
        df_exploded = df.select("results.*")
        df_exploded = df_exploded.withColumn('dat_load', lit(dat_load))
        df_exploded = df_exploded.withColumn(
            "dat_load", to_timestamp("dat_load", "yyyy-MM-dd HH:mm:ss"))
        df_exploded = df_exploded.dropDuplicates()

        print(f'transform_table: {df_exploded.count()}')

        return df_exploded

    def check_table(self):
        s3 = boto3.client('s3', region_name='us-east-1')
        try:
            response = s3.list_objects(
                Bucket=self.bucket,
                Prefix=f's3: // {self.bucket}/bronze_data/{self.table}',
                MaxKeys=1)
            if response.get('Contents')[0].get('Size') > 0:
                return True
            return response
        except Exception:
            return False

    def write_table(self, spark, df, table_info):
        if table_info:
            print('New Table - no upsert')
            df.write.mode("overwrite").format("delta").save(
                f's3://{self.bucket}/bronze_data/{self.table}')
            # deltaTable = DeltaTable.forPath(
            #    f's3://{self.bucket}/bronze_data/{self.table}')
            # deltaTable.generate("symlink_format_manifest")
            print(f'write_table: {df.count()}')
        else:
            print('upsert')
            bronze_table_path = f's3a://{self.bucket}/bronze_data/{self.table}'
            bronze = DeltaTable.forPath(spark, bronze_table_path)
            print('start upsert')
            bronze.alias('bronze_table').merge(
                df.alias('raw_table'),
                f"bronze_table.{self.idt_key}=raw_table.{self.idt_key}"
            ).whenMatchedUpdateAll()\
                .whenNotMatchedInsertAll()\
                .execute()
            # print('start log')
            # bronze.generate("symlink_format_manifest")
            spark.sql(
                f'GENERATE symlink_format_manifest FOR TABLE delta.`{bronze_table_path}`')

            df1 = spark.read.format("delta").load(f'{bronze_table_path}')
            print(f'write_table: {df1.count()}')

    def main(self):
        pass


if __name__ == "__main__":
    tables = [
        {"nam_table": "films", "id_table": "episode_id"},
        {"nam_table": "planets", "id_table": "name"},
        {"nam_table": "species", "id_table": "name"},
        {"nam_table": "starships", "id_table": "name"},
        {"nam_table": "vehicles", "id_table": "name"},
        {"nam_table": "people", "id_table": "name"},
    ]

    for i in tables:
        ingestion = IngestionBroze(
            'star-wars-etl-dev', i.get('nam_table'), i.get('id_table'))
        spark = ingestion.spark_session()

        # load table
        df = ingestion.load_table(spark)

        # transform table
        df = ingestion.transform_table(df)

        # check table
        table_info = ingestion.check_table()

        # write bucket
        ingestion.write_table(spark, df, table_info)