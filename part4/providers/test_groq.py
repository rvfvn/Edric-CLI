from .groq import GroqProvider


provider = GroqProvider()

response = provider.generate(
    [
        {
            "role": "user",
            "content": "Write a Python function that adds two numbers.",
        }
    ]
)

print("Response:")
print(response["content"])