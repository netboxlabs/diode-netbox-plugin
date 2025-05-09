

def create_client(client_name, scope    ):
    ret = {
        "client_name": "client_name",
        "client_id": "client_id",
        "client_secret": "client_secret",
        "scope": "scope",
        "created_at": "2025-03-14T15:16:17Z"
    }
    return ret


def delete_client(client_id):
    pass


def list_clients():
    ret = {
        "data": [
            {
                "client_name": "My Agent 1",
                "client_id": "my-agent-1-a038dfef",
                "scope": "diode:ingest",
                "created_at": "2025-03-14T15:16:17Z"
            },
            {
                "client_name": "US East 12",
                "client_id": "us-east-12-f00fa3cd",
                "scope": "diode:ingest",
                "created_at": "2025-04-15T10:11:00Z"
            }
        ],
        "next_page_token": "3",
    }
    return ret["data"]


