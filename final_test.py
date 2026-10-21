import httpx
import asyncio

async def test():
    try:
        async with httpx.AsyncClient() as client:
            print('Sending request to /ask...')
            # Using one of the PRD examples
            resp = await client.post('http://localhost:7860/ask', 
                                  json={'question': 'Where can I go running tomorrow evening near Koregaon Park?'},
                                  timeout=120.0)
            print(f'Status: {resp.status_code}')
            print(f'Response: {resp.text}')
    except Exception as e:
        print(f'Error: {e}')

if __name__ == '__main__':
    asyncio.run(test())
