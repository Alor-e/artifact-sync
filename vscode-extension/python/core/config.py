import os
import argparse
from dataclasses import dataclass
from core.abstractions import ModelProvider

@dataclass
class AgentConfig:
    """Configuration for the analysis agent"""
    provider: ModelProvider
    model_name: str
    api_key: str
    root_path: str
    max_depth: int = 3
    max_retries: int = 3
    
    @classmethod
    def from_env(cls, args: 'argparse.Namespace' = None) -> 'AgentConfig':
        """
        Create config from environment variables, with optional overrides from command-line arguments.
        
        Priority:
        1. Command-line arguments (if provided)
        2. Environment variables
        3. Default values
        """
        
        def get_config_value(arg_name: str, env_var: str, default: any = None) -> any:
            """Helper to get a configuration value from args, env, or default."""
            if arg_name and args and hasattr(args, arg_name) and getattr(args, arg_name) is not None:
                return getattr(args, arg_name)
            return os.getenv(env_var, default)

        # Determine provider based on available API keys or command-line arg
        provider_str = get_config_value('provider', 'MODEL_PROVIDER', None)
        provider = None
        model_name = None
        api_key = None

        if provider_str:
            try:
                provider = ModelProvider(provider_str.upper())
            except ValueError:
                raise ValueError(f"Invalid provider specified: {provider_str}")
        
        # Determine provider from environment if not specified
        if not provider:
            if get_config_value('api_key', "GEMINI_API_KEY"):
                provider = ModelProvider.GEMINI
            elif get_config_value('api_key', "OPENAI_API_KEY"):
                provider = ModelProvider.OPENAI
            elif get_config_value('api_key', "ANTHROPIC_API_KEY"):
                provider = ModelProvider.ANTHROPIC
        
        if not provider:
            raise ValueError("No model provider specified or API key found in environment variables")

        # Get model and API key based on the determined provider
        if provider == ModelProvider.GEMINI:
            model_name = get_config_value('model_name', 'GEMINI_MODEL', 'gemini-pro')
            api_key = get_config_value('api_key', 'GEMINI_API_KEY')
        elif provider == ModelProvider.OPENAI:
            model_name = get_config_value('model_name', 'OPENAI_MODEL', 'gpt-4')
            api_key = get_config_value('api_key', 'OPENAI_API_KEY')
        elif provider == ModelProvider.ANTHROPIC:
            model_name = get_config_value('model_name', 'ANTHROPIC_MODEL', 'claude-3-sonnet-20240229')
            api_key = get_config_value('api_key', 'ANTHROPIC_API_KEY')
        
        if not api_key:
            raise ValueError(f"API key for {provider.value} not found.")

        root_path = get_config_value('root_path', 'ROOT_PATH')
        if not root_path:
            raise ValueError("ROOT_PATH must be set via command-line or environment variable")
        
        return cls(
            provider=provider,
            model_name=model_name,
            api_key=api_key,
            root_path=root_path,
            max_depth=int(get_config_value('max_depth', 'MAX_DEPTH', '3')),
            max_retries=int(get_config_value('max_retries', 'MAX_RETRIES', '3'))
        )