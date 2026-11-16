import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
AVIATION_STACK_API_KEY = os.getenv("AVIATION_STACK_API_KEY")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

def get_llm():
    # Groq's free tier allows few tokens per minute. Retrying (the client waits
    # for the time Groq asks for) lets a request ride out a short rate limit.
    return ChatGroq(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        max_retries=int(os.getenv("GROQ_MAX_RETRIES", "6")),
    )