from .ollama import OllamaProvider


provider = OllamaProvider()

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
