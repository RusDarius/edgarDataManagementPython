"""
Token Estimator Script
----------------------
Given a string input, estimates the number of tokens required for the following models:
- OpenAI GPT-4.1 (gpt-4-1106-preview)
- OpenAI GPT-4o (gpt-4o)
- Anthropic Claude 3.5 Sonnet (claude-3.5-sonnet-20240620)
- Anthropic Claude 3 Opus (claude-3-opus-20240229)

Usage:
    python token_estimator.py "your text here"

Requires:
    - tiktoken (for OpenAI models)
    - anthropic (for Claude models, or fallback to simple estimate)
"""

import sys

try:
    import tiktoken
except ImportError:
    tiktoken = None


def estimate_gpt_tokens(text, model_name):
    if not tiktoken:
        print("tiktoken not installed. Install with: pip install tiktoken")
        return None
    try:
        enc = tiktoken.encoding_for_model(model_name)
    except Exception:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def estimate_claude_tokens(text):
    # Claude uses ~1 token per 3.5 characters (English average)
    # This is a rough estimate, as Anthropic does not provide a public tokenizer
    return int(len(text) / 3.5)


def main():
    if len(sys.argv) < 2:
        print("Usage: python token_estimator.py 'your text here'")
        sys.exit(1)
    text = sys.argv[1]
    print("\nToken estimates for input:\n-------------------------")
    print(text)
    print("\n---\n")
    # OpenAI GPT models
    gpt_models = [
        ("gpt-4-1106-preview", "OpenAI GPT-4.1 (gpt-4-1106-preview)"),
        ("gpt-4o", "OpenAI GPT-4o (gpt-4o)"),
    ]
    for model, label in gpt_models:
        tokens = estimate_gpt_tokens(text, model)
        print(f"{label}: {tokens if tokens is not None else 'N/A'} tokens")
    # Claude models (estimate)
    claude_models = [
        ("claude-3-opus-20240229", "Claude 3 Opus (claude-3-opus-20240229)"),
        (
            "claude-3.5-sonnet-20240620",
            "Claude 3.5 Sonnet (claude-3.5-sonnet-20240620)",
        ),
    ]
    for model, label in claude_models:
        tokens = estimate_claude_tokens(text)
        print(f"{label}: ~{tokens} tokens (estimated)")


if __name__ == "__main__":
    main()
