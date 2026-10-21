import re

file_path = r'C:\Users\deepe\Downloads\GeoAI\app\context.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# We use a very broad pattern to find the weather and aq block
pattern = r'            if key == "weather" and res.data:.*?val = f"Current US-AQI: {aqi}"'
replacement = """            if key == "weather" and res.data:
                # Summarize forecast for evidence
                data = res.data[0] if isinstance(res.data, list) else res.data
                w_data = data.get("hourly", {})
                t_list = w_data.get("temperature_2m", [])
                temp = t_list[0] if isinstance(t_list, list) and t_list else (t_list.get(0) if isinstance(t_list, dict) else "unknown")
                
                c_list = w_data.get("weather_code", [])
                code = c_list[0] if isinstance(c_list, list) and c_list else (c_list.get(0) if isinstance(c_list, dict) else "unknown")
                val = f"Current temp: {temp}C, Weather code: {code}"
            elif key == "aq" and res.data:
                # Handle list of results for multiple coordinates
                data = res.data[0] if isinstance(res.data, list) else res.data
                forecast = data.get("forecast", data) if isinstance(data, dict) else data
                final_data = forecast[0] if isinstance(forecast, list) else forecast
                aq_data = final_data.get("hourly", {}).get("us_aqi", [])
                aqi = aq_data[0] if isinstance(aq_data, list) and aq_data else (aq_data.get(0) if isinstance(aq_data, dict) else "unknown")
                val = f"Current US-AQI: {aqi}" """

new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)
