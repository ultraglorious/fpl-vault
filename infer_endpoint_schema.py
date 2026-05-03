from typing import Dict
from api_client import APIClient

def UnknownDataType(Exception):
    pass

def infer_response_schema(json_data: dict):
    if len(json_data) == 0:
        raise ValueError("API response is empty")
    
    if isinstance(json_data, list):
        # If JSON is a list type then extract schema from the first item in the list
        # (assuming all items have the same structure)
        sample_item = json_data[0]
        schema: Dict[str, str] = {}

        for key, value in sample_item.items():
            if isinstance(value, dict):
                schema[key] = "dict"
            elif isinstance(value, list):
                schema[key] = "list"
            elif isinstance(value, int):
                schema[key] = "int"
            elif isinstance(value, float):
                schema[key] = "float"
            elif isinstance(value, str):
                schema[key] = "str"
            else:
                schema[key] = "unknown"
    elif isinstance(json_data, dict):
        # If JSON is a dictionary type then we need to navigate down into the dictionary
        schema: Dict[str, str] = {}
        for key, value in json_data.items():
            pass


    return schema

if __name__ == '__main__':
    client = APIClient(base_url='https://fantasy.premierleague.com/api/')
    response = client.get(endpoint='bootstrap-static')
    print(len(response))
    for key, value in response.items():
        print(f'key:{key}, value_type:{type(value)}')
        # print(value)
    print(response['teams'][0])
    print('yo')
    # schema = infer_response_schema(response)
    # print(schema)