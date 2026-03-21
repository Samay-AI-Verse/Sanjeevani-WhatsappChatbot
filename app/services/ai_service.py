import httpx
from groq import Groq
from typing import Dict
from ..core.config import GROQ_API_KEY, GROQ_MODEL
from ..core.logger import logger

# Groq Setup
groq_client = Groq(api_key=GROQ_API_KEY, http_client=httpx.Client()) if GROQ_API_KEY else None

def get_conversational_reply(user_text: str, user_profile: Dict) -> str:
    """Generates a conversational response using LLM when the user is just chatting."""
    if not groq_client: 
        return "I'm sorry, my AI brain is currently offline. How can I help you order medicine?"
        
    language = user_profile.get("language", "English")
    name = user_profile.get("name", "User")
    
    prompt = f"""
    You are 'Sanjeevani Care', a highly professional, focused, and polite AI Pharmacy Manager.
    You are currently chatting with {name}. Their preferred language is {language}.
    
    INSTRUCTIONS:
    1. YOUR ULTIMATE GOAL IS TO TAKE AN ORDER OR HELP THEM TRACK AN EXISTING ORDER.
    2. Respond to the user's casual or roundabout message in 1 very brief, polite sentence.
    3. IMMEDIATELY after your polite acknowledgment, forcefully but politely steer the conversation back to your goal: Ask them what medicines they would like to order today, or if they need help tracking their current order.
    4. DO NOT offer arbitrary medical advice, DO NOT ask open-ended health questions, DO NOT chat aimlessly.
    5. Use friendly emojis appropriately (⚕️, 🩺, 😊, etc.)
    6. Reply ONLY in {language}.
    
    Example response format:
    "Hello {name}, I'm doing well, thank you! 😊 How can I assist you today? Would you like me to place a medicine order for you, or track an existing one? 💊"
    
    User message: "{user_text}"
    """
    
    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": prompt}],
            temperature=0.6,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Conversational LLM Error: {e}")
        return "I'm sorry, I didn't quite catch that. How can I help you regarding your health today?"
