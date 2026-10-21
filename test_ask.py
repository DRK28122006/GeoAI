import httpx
import asyncio
import traceback

async def test():
    try:
        async with httpx.AsyncClient() as client:
            print('Sending request to /ask...')
            resp = await client.post('http://localhost:7860/ask', 
                                  json={'question': 'I live in JP Nagar 5th Phase (opp Kalparakha Apartments) in Bangalore. I want to play in a park nearby around 2pm. Where should I go?'},
                                  timeout=120.0)
            print(f'Status: {resp.status_code}')
            print(f'Response: {resp.text}')
    except Exception as e:
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test())
