import asyncio
import json
from app.pipeline import pipeline

async def run_test():
    question = "I have mild asthma, where is a good place for a walk in JP Nagar 5th Phase around 2pm?"
    print(f"Question: {question}")
    try:
        result = await pipeline.run(question)
        print("\n--- Pipeline Result ---")
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error running pipeline: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_test())
