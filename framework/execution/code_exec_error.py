# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path
import traceback
import argparse

if __package__:
    from .sandbox import run_analysis
else:
    from sandbox import run_analysis

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exec_dir", required=True)
    parser.add_argument("--csv_dir", required=True)
    parser.add_argument("--table_txt_dir", required=True)
    parser.add_argument("--output_base_dir", required=True)
    args = parser.parse_args()

    exec_dir = Path(args.exec_dir)
    csv_dir = Path(args.csv_dir)
    table_format3_dir = Path(args.table_txt_dir)
    output_base_dir = Path(args.output_base_dir)
    output_base_dir.mkdir(parents=True, exist_ok=True)

    existing_tables = {d.name for d in output_base_dir.iterdir() if d.is_dir()}
    print(f"Processed tables: {len(existing_tables)}")
    total_queries = 0
    successful_queries = 0

    for qc_file in exec_dir.glob('table_*_modeling.json'):
        table_name = qc_file.stem.replace('_modeling', '')
        if table_name in existing_tables:
            continue
        print(f"Processing file {qc_file}")
        with open(qc_file, 'r', encoding='utf-8') as f:
            recommendations = json.load(f)
        output_dir = output_base_dir / table_name
        output_dir.mkdir(parents=True, exist_ok=True)
        data_file_csv = (csv_dir / f"{table_name}.csv").resolve()
        data_file_csv.relative_to(csv_dir.resolve())
        output_dir.resolve().relative_to(output_base_dir.resolve())
        for idx, recommendation in enumerate(recommendations):
            total_queries += 1
            code = recommendation.get('code', '')
            if not code:
                print(f"No code found in recommendation {idx + 1}. Skipping.")
                continue
            query_number = idx + 1
            output_filename = f"query{query_number}"
            try:
                run_analysis(code, data_file_csv, output_dir, output_filename)
                print(f"Sandbox results saved: {output_dir / output_filename}")
                successful_queries += 1
            except TimeoutError as e:
                print(f"Timeout occurred while processing table '{table_name}' query {query_number}: {e}")
                error_filename = output_dir / f"query{query_number}_error.txt"
                table_format2_file = table_format3_dir / f"{table_name}.txt"
                if table_format2_file.exists():
                    with open(table_format2_file, 'r', encoding='utf-8') as tf:
                        table_content = tf.read()
                else:
                    table_content = f"Table file {table_format2_file} does not exist."
                with open(error_filename, 'w', encoding='utf-8') as error_file:
                    error_file.write(f"Table Name: {table_name}\n")
                    error_file.write(f"{table_content}\n\n")
                    error_file.write(f"Query Number: {query_number}\n")
                    error_file.write(f"Query: {recommendation.get('query', 'N/A')}\n\n")
                    error_file.write(f"Code:\n{code}\n\n")
                    error_file.write(f"Error Message:\n{str(e)}\n\n")
                print(f"Timeout error details saved to {error_filename}")
            except Exception as e:
                print(f"Error occurred while processing table '{table_name}' query {query_number}:")
                traceback.print_exc()
                error_filename = output_dir / f"query{query_number}_error.txt"
                table_format2_file = table_format3_dir / f"{table_name}.txt"
                if table_format2_file.exists():
                    with open(table_format2_file, 'r', encoding='utf-8') as tf:
                        table_content = tf.read()
                else:
                    table_content = f"Table file {table_format2_file} does not exist."
                with open(error_filename, 'w', encoding='utf-8') as error_file:
                    error_file.write(f"Table Name: {table_name}\n")
                    error_file.write(f"{table_content}\n\n")
                    error_file.write(f"Query Number: {query_number}\n")
                    error_file.write(f"Query: {recommendation.get('query', 'N/A')}\n\n")
                    error_file.write(f"Code:\n{code}\n\n")
                    error_file.write(f"Error Message:\n{traceback.format_exc()}\n\n")
                print(f"Error details saved to {error_filename}")
    print(f"\nExecution completed.")
    print(f"Total queries processed: {total_queries}")
    print(f"Successful queries: {successful_queries}")
    print(f"Failed queries: {total_queries - successful_queries}")

if __name__ == "__main__":
    main()
