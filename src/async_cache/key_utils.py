"""
Key utilities for the async_cache package.

Provides functions for generating and composing cache keys.
"""

import functools
from dataclasses import dataclass
from typing import Any, Dict, Callable

from async_cache.exceptions import InvalidCacheKeyError

# Type for context dictionaries used in custom cache key generation
CacheKeyContext = Dict[str, Any]

# Type for cache key generation functions
CacheKeyFn = Callable[[CacheKeyContext], str]


@dataclass
class CacheKeyComponent:
    """A component of a cache key, used for composition."""
    name: str
    value: Any


def compose_key_functions(*funcs: CacheKeyFn) -> CacheKeyFn:
    """
    Compose multiple key functions into a single function.
    
    Args:
        *funcs: One or more cache key functions to compose
        
    Returns:
        A new function that applies all key functions and joins the results
        
    Example:
    
    ```python
    # Create a composite key function from two lambdas
    key_fn = compose_key_functions(
        lambda ctx: ctx["func_name"],
        lambda ctx: str(ctx["kwargs"].get("user_id", "anonymous"))
    )
    
    # Use it to generate a key
    key = key_fn({"func_name": "get_data", "kwargs": {"user_id": 123}})
    # key will be 'get_data:123'
    ```
    """
    if not funcs:
        raise ValueError("At least one key function must be provided")

    def composed(context: CacheKeyContext) -> str:
        parts = []
        for func in funcs:
            part = func(context)
            if not isinstance(part, str):
                raise InvalidCacheKeyError(f"Key function {func.__name__} returned {type(part)}, expected str")
            parts.append(part)
        return ":".join(parts)

    return composed


def extract_key_component(key_path: str) -> CacheKeyFn:
    """
    Create a key function that extracts a component from the context.
    
    Args:
        key_path: Dot-notation path to extract (e.g., "kwargs.user_id")
        
    Returns:
        A function that extracts the value at the specified path
        
    Example:
        
    ```python
    # Create an extractor for a specific path
    key_fn = extract_key_component("kwargs.user_id")
    
    # Use it to extract a value
    key = key_fn({"kwargs": {"user_id": 123}})
    # key will be '123'
    ```
    """

    def extract(context: CacheKeyContext) -> str:
        parts = key_path.split(".")
        value = context

        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return "none"

        return str(value)

    return extract


def key_component(name: str = None) -> Callable[[CacheKeyFn], CacheKeyFn]:
    """
    Decorator for creating named cache key components.
    
    Args:
        name: Optional name for the component
        
    Returns:
        A decorator function
        
    Example:
        
    ```python
    # Create a named component for version extraction
    @key_component("version")
    def version_key(context):
        return context.get("metadata", {}).get("version", "1.0")
        
    # Using the decorated function
    key = version_key({"metadata": {"version": "2.0"}})
    # key will be 'version:2.0'
    ```
    """

    def decorator(func: CacheKeyFn) -> CacheKeyFn:
        @functools.wraps(func)
        def wrapper(context: CacheKeyContext) -> str:
            result = func(context)
            component_name = name or func.__name__
            return f"{component_name}:{result}"

        return wrapper

    return decorator
