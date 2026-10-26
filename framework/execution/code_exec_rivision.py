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

def main(args):
    exec_dir = Path(args.exec_dir)
    csv_dir = Path(args.csv_dir)
    table_format_dir = Path(args.table_format_dir)
    output_base_dir = Path(args.output_base_dir)
    output_base_dir.mkdir(parents=True, exist_ok=True)
    existing_tables = {d.name for d in output_base_dir.iterdir() if d.is_dir()}
    total_queries = 0
    successful_queries = 0
    for table_dir in exec_dir.glob('table_*'):
        table_name = table_dir.name
        if table_name in existing_tables:
            continue
        print(f"Processing table {table_name}")
        data_file_csv = (csv_dir / f"{table_name}.csv").resolve()
        data_file_csv.relative_to(csv_dir.resolve())
        output_dir = output_base_dir / f"{table_name}"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir.resolve().relative_to(output_base_dir.resolve())
        try:
            for query_file in table_dir.glob('query*.json'):
                total_queries += 1
                try:
                    with open(query_file, 'r', encoding='utf-8') as f:
                        query_data = json.load(f)
                    code = query_data.get('optimized_code', '')
                    if not code:
                        print(f"No code found in {query_file}, skipping")
                        continue
                    query_number = query_file.stem.replace('query', '').replace('_error', '')
                    output_filename = f"query{query_number}"
                    try:
                        run_analysis(code, data_file_csv, output_dir, output_filename)
                        print(f"Sandbox results saved: {output_dir / output_filename}")
                        successful_queries += 1
                    except TimeoutError as e:
                        print(f"Timeout occurred while processing {query_file.name}")
                        error_filename = output_dir / f"query{query_number}_error.txt"
                        table_format2_file = table_format_dir / f"{table_name}.txt"
                        if table_format2_file.exists():
                            with open(table_format2_file, 'r', encoding='utf-8') as tf:
                                table_content = tf.read()
                        else:
                            table_content = f"Table file {table_format2_file} does not exist."
                        with open(error_filename, 'w', encoding='utf-8') as error_file:
                            error_file.write(f"Table Name: {table_name}\n")
                            error_file.write(f"{table_content}\n\n")
                            error_file.write(f"Query Number: {query_number}\n")
                            error_file.write(f"Query: {query_data.get('query', 'N/A')}\n\n")
                            error_file.write(f"Code:\n{code}\n\n")
                            error_file.write(f"Error Message:\nTimeout Error: Execution exceeded timeout of 120 seconds\n")
                        print(f"Timeout error details saved to {error_filename}")
                    except Exception as e:
                        print(f"Error occurred while processing {query_file.name}")
                        error_filename = output_dir / f"query{query_number}_error.txt"
                        table_format2_file = table_format_dir / f"{table_name}.txt"
                        if table_format2_file.exists():
                            with open(table_format2_file, 'r', encoding='utf-8') as tf:
                                table_content = tf.read()
                        else:
                            table_content = f"Table file {table_format2_file} does not exist."
                        with open(error_filename, 'w', encoding='utf-8') as error_file:
                            error_file.write(f"Table Name: {table_name}\n")
                            error_file.write(f"{table_content}\n\n")
                            error_file.write(f"Query Number: {query_number}\n")
                            error_file.write(f"Query: {query_data.get('query', 'N/A')}\n\n")
                            error_file.write(f"Code:\n{code}\n\n")
                            error_file.write(f"Error Message:\n{traceback.format_exc()}\n")
                        print(f"Error details saved to {error_filename}")
                except Exception as e:
                    print(f"Error processing file {query_file.name}: {str(e)}")
                    continue
        except Exception as e:
            print(f"Error processing table directory {table_dir}: {str(e)}")
            continue
    print(f"\nFinal Execution Statistics:")
    print(f"Total queries processed: {total_queries}")
    print(f"Successful queries: {successful_queries}")
    print(f"Failed queries: {total_queries - successful_queries}")
    if total_queries > 0:
        print(f"Success rate: {(successful_queries/total_queries*100):.2f}%")
    else:
        print("Success rate: N/A (no queries processed)")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exec_dir', required=True)
    parser.add_argument('--csv_dir', required=True)
    parser.add_argument('--table_format_dir')
    parser.add_argument('--output_base_dir')
    args = parser.parse_args()
    main(args)
