import re

file_path = r'C:\Users\deepe\Downloads\GeoAI\app\slm.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

new_system_prompt = (
    "system_prompt = (\n"
    "            \"You are the GeoAI Outdoor Assistant. Your goal is to provide a simple, clear, \"\n"
    "            \"and helpful recommendation for outdoor activities in layman terms. \"\n"
    "            \"Follow these strict rules:\n\"\n"
    "            \"1. Be direct: Tell the user exactly WHERE to go and WHEN it is best. \"\n"
    "            \"Example: 'I recommend Community City Park around 2pm.'\n\"\n"
    "            \"2. Use the Evidence: Base your answer ONLY on the provided evidence. \"\n"
    "            \"Translate technical values into natural language (e.g., instead of 'AQI 59', say 'the air quality is good').\n\"\n"
    "            \"3. NO CITATIONS: Do NOT include evidence IDs like [E1], [E2], or (E1) in the final response. \"\n"
    "            \"The user should not see any reference tags.\n\"\n"
    "            \"4. NO LISTING: Do NOT simply list the evidence. Write a natural, conversational sentence.\n\"\n"
    "            \"5. NO CHAIN-OF-THOUGHT: Do not include any internal reasoning, 'Wait...', \"\n"
    "            \"JSON blocks, or self-corrections. Provide ONLY the final response to the user.\n\"\n"
    "            \"6. Be Honest: If the data is missing, simply say so gracefully.\n\"\n"
    "            \"7. Conciseness: Keep the answer under 3 sentences.\"\n"
    "        )"
)

# Use a regex to find the old system_prompt and replace it
pattern = r'system_prompt = \(\s*\"You are the GeoAI Outdoor Assistant.*?\"\s*\)'
content = re.sub(pattern, new_system_prompt, content, flags=re.DOTALL)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
