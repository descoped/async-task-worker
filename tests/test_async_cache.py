"""
Test Suite for async_cache package

Tests the core components of the async_cache package including key generation
and memory adapter functionality.
"""

import asyncio
import datetime
import math
import uuid
from typing import NamedTuple

import pytest

from async_cache import AsyncCache, InvalidCacheKeyError, compose_key_functions, extract_key_component, key_component
from async_cache.adapters import MemoryCacheAdapter
from async_cache.key_utils import CacheKeyContext


# Test fixture for the cache
@pytest.fixture
async def memory_cache():
    """Provides a memory cache adapter for testing."""
    adapter = MemoryCacheAdapter(max_size=10)
    cache = AsyncCache(adapter=adapter, default_ttl=60, enabled=True)
    return cache


# Simple test objects for serialization testing
class SimpleObject:
    """Simple class with __dict__ for testing serialization."""

    def __init__(self, value):
        self.value = value
        self.name = "test"


class SlotObject:
    """Simple class with __slots__ for testing serialization."""

    __slots__ = ["value", "name"]

    def __init__(self, value):
        self.value = value
        self.name = "test"


class CustomTuple(NamedTuple):
    """Named tuple for testing serialization."""

    id: str
    value: int


# Basic cache functionality tests
@pytest.mark.asyncio
async def test_basic_cache_operations(memory_cache):
    """Test basic cache operations with primitive types."""
    # Test function and arguments
    fn_name = "test_func"
    args = (1, 2, "test")
    kwargs = {"a": 1, "b": "test"}
    result = {"result": "value", "count": 42}

    # Set in cache
    success = await memory_cache.set(fn_name, args, kwargs, result)
    assert success is True

    # Get from cache
    hit, cached_value = await memory_cache.get(fn_name, args, kwargs)
    assert hit is True
    assert cached_value == result

    # Invalidate cache entry
    success = await memory_cache.invalidate(fn_name, args, kwargs)
    assert success is True

    # Should be gone now
    hit, _ = await memory_cache.get(fn_name, args, kwargs)
    assert hit is False


@pytest.mark.asyncio
async def test_key_generation_consistency(memory_cache):
    """Test that cache key generation is consistent for the same inputs."""
    fn_name = "test_func"
    args = (1, 2, "test")
    kwargs = {"a": 1, "b": "test"}

    # Generate key context
    context = memory_cache._build_context(fn_name, args, kwargs)

    # Generate key multiple times using default function
    key1 = AsyncCache.default_key_fn(context)
    key2 = AsyncCache.default_key_fn(context)

    # Keys should be identical for identical inputs
    assert key1 == key2

    # Different argument order in kwargs shouldn't matter
    kwargs_reordered = {"b": "test", "a": 1}
    context_reordered = memory_cache._build_context(fn_name, args, kwargs_reordered)
    key3 = AsyncCache.default_key_fn(context_reordered)
    assert key1 == key3


@pytest.mark.asyncio
async def test_cache_with_complex_types(memory_cache):
    """Test caching with more complex data types using msgpack."""
    fn_name = "complex_func"

    # Test with various complex types
    args = ()
    kwargs = {
        "simple_object": SimpleObject(42),
        "slot_object": SlotObject(43),
        "tuple_object": CustomTuple(id="test", value=44),
        "datetime": datetime.datetime.now(),
        "uuid": uuid.uuid4(),
        "nested": {"list": [1, 2, {"a": 3}], "dict": {"key": [4, 5, 6]}},
    }

    result = {"status": "success", "data": kwargs}

    # Try to cache this complex data
    success = await memory_cache.set(fn_name, args, kwargs, result)
    assert success is True

    # Retrieve it back
    hit, cached_result = await memory_cache.get(fn_name, args, kwargs)
    assert hit is True

    # Complex objects might be converted to dictionaries or other serializable forms
    # So we check that the structure is maintained
    assert "status" in cached_result
    assert cached_result["status"] == "success"
    assert "data" in cached_result


@pytest.mark.asyncio
async def test_cache_ttl(memory_cache):
    """Test that cache entries expire after TTL."""
    # Override with a very short TTL
    memory_cache.default_ttl = 0.1  # 100ms

    fn_name = "ttl_func"
    args = ()
    kwargs = {}
    result = {"result": "This should expire quickly"}

    # Set in cache
    await memory_cache.set(fn_name, args, kwargs, result)

    # Should be available immediately
    hit, _ = await memory_cache.get(fn_name, args, kwargs)
    assert hit is True

    # Wait for expiration
    await asyncio.sleep(0.15)

    # Should be gone now
    hit, _ = await memory_cache.get(fn_name, args, kwargs)
    assert hit is False


@pytest.mark.asyncio
async def test_cache_max_size(memory_cache):
    """Test that cache evicts items when reaching max size."""
    # Cache adapter has max_size=10 from fixture

    # Add 15 items (should cause 5 evictions)
    for i in range(15):
        fn_name = f"func_{i}"
        await memory_cache.set(fn_name, (), {}, {"index": i})

    # Check how many items we can still retrieve
    # The first 5 should be evicted, and 10 should remain
    hit_count = 0
    for i in range(15):
        fn_name = f"func_{i}"
        hit, _ = await memory_cache.get(fn_name, (), {})
        if hit:
            hit_count += 1

    # Should have 10 hits (cache max size)
    assert hit_count == 10

    # First few items should be evicted, check one
    hit, _ = await memory_cache.get("func_0", (), {})
    assert hit is False


@pytest.mark.asyncio
async def test_cache_clear(memory_cache):
    """Test clearing the entire cache."""
    # Add some items
    for i in range(5):
        fn_name = f"func_{i}"
        await memory_cache.set(fn_name, (), {}, {"index": i})

    # Verify they're in the cache
    hit, _ = await memory_cache.get("func_0", (), {})
    assert hit is True

    # Clear the cache
    await memory_cache.clear()

    # Verify everything is gone
    for i in range(5):
        fn_name = f"func_{i}"
        hit, _ = await memory_cache.get(fn_name, (), {})
        assert hit is False


@pytest.mark.asyncio
async def test_non_serializable_object(memory_cache):
    """Test behavior with a non-serializable object."""
    # Create an object with a circular reference, which can't be serialized
    circular = {"self": None}
    circular["self"] = circular

    # Attempt to cache it
    success = await memory_cache.set("circular_func", (), {}, circular)

    # Should fail gracefully
    assert success is False


@pytest.mark.asyncio
async def test_different_kwargs_order(memory_cache):
    """Test that kwargs order doesn't affect caching."""
    fn_name = "order_test"
    args = (1, 2, 3)

    # Two identical kwargs dictionaries with different order
    kwargs1 = {"a": 1, "b": 2, "c": 3}
    kwargs2 = {"c": 3, "a": 1, "b": 2}

    # Cache with the first order
    result = {"result": "test"}
    await memory_cache.set(fn_name, args, kwargs1, result)

    # Retrieve with the second order
    hit, cached_result = await memory_cache.get(fn_name, args, kwargs2)

    # Should hit the cache and get the same result
    assert hit is True
    assert cached_result == result


@pytest.mark.asyncio
async def test_large_object_serialization(memory_cache):
    """Test serialization of a large object that exceeds the size limit."""
    # Set a small max size for testing
    memory_cache.max_serialized_size = 100  # 100 bytes

    # Create a large object
    large_object = {"data": "x" * 1000}  # Will be > 100 bytes when serialized

    # Try to cache it
    success = await memory_cache.set("large_func", (), {}, large_object)

    # Should fail due to size limit
    assert success is False


@pytest.mark.asyncio
async def test_fallback_to_str(memory_cache):
    """Test the fallback to string representation."""

    # Define a class that msgpack can't serialize directly
    class UnserializableClass:
        def __init__(self):
            self.unpickable_attr = lambda x: x

        def __str__(self):
            return "UnserializableClass instance"

    unserializable = UnserializableClass()

    # With fallback enabled, this should work
    memory_cache.fallback_to_str = True
    success = await memory_cache.set("fallback_func", (), {}, unserializable)
    assert success is True

    # With fallback disabled, this should fail
    memory_cache.fallback_to_str = False
    success = await memory_cache.set("no_fallback_func", (), {}, unserializable)
    assert success is False


@pytest.mark.asyncio
async def test_cache_disabled(memory_cache):
    """Test behavior when cache is explicitly disabled."""
    # Disable the cache
    memory_cache.enabled = False

    fn_name = "disabled_test"
    result = {"status": "success"}

    # Try to set in the cache
    success = await memory_cache.set(fn_name, (), {}, result)
    assert success is False

    # Try to get from the cache
    hit, _ = await memory_cache.get(fn_name, (), {})
    assert hit is False


@pytest.mark.asyncio
async def test_cache_error_handling(memory_cache):
    """Test error handling during cache operations."""
    # Create a faulty adapter method to simulate errors
    original_get = memory_cache.adapter.get

    # Replace with a method that raises an exception
    # noinspection PyUnusedLocal
    async def faulty_get(key):
        raise Exception("Simulated cache error")

    try:
        # Apply the fault
        memory_cache.adapter.get = faulty_get

        # Try to get from cache - should handle error gracefully
        hit, _ = await memory_cache.get("error_test", (), {})
        assert hit is False
    finally:
        # Restore original method
        memory_cache.adapter.get = original_get


@pytest.mark.asyncio
async def test_custom_ttl_override(memory_cache):
    """Test overriding the default TTL for specific entries."""
    # Set default TTL to a longer time
    memory_cache.default_ttl = 10  # 10 seconds

    # Store an item with a custom short TTL
    custom_ttl = 1  # 1 second
    await memory_cache.set("short_ttl", (), {}, "should expire quickly", ttl=custom_ttl)

    # Store another with default TTL
    await memory_cache.set("default_ttl", (), {}, "should last longer")

    # Check that both are initially available
    hit1, _ = await memory_cache.get("short_ttl", (), {})
    hit2, _ = await memory_cache.get("default_ttl", (), {})
    assert hit1 is True
    assert hit2 is True

    # Wait for the custom TTL to expire
    await asyncio.sleep(1.1)

    # Short TTL should have expired, default TTL should still be available
    hit1, _ = await memory_cache.get("short_ttl", (), {})
    hit2, _ = await memory_cache.get("default_ttl", (), {})
    assert hit1 is False
    assert hit2 is True


@pytest.mark.asyncio
async def test_none_ttl_no_expiry(memory_cache):
    """Test that a TTL of None means no expiration."""
    # Set an item with TTL of None (no expiry)
    await memory_cache.set("no_expiry", (), {}, "should not expire", ttl=None)

    # Wait some time
    await asyncio.sleep(0.2)

    # Should still be available
    hit, _ = await memory_cache.get("no_expiry", (), {})
    assert hit is True


@pytest.mark.asyncio
async def test_edge_case_floats_and_special_values(memory_cache):
    """Test cache behavior with edge case float values."""
    # Test with special float values
    special_floats = {"inf": float("inf"), "neg_inf": float("-inf"), "nan": float("nan")}

    # Store in cache
    await memory_cache.set("special_floats", (), {}, special_floats)

    # Retrieve from cache
    hit, value = await memory_cache.get("special_floats", (), {})
    assert hit is True

    # Check special values (NaN doesn't compare equal to itself, so check isnan)
    assert value["inf"] == float("inf")
    assert value["neg_inf"] == float("-inf")
    assert math.isnan(value["nan"])


@pytest.mark.asyncio
async def test_concurrent_cache_access():
    """Test concurrent access to the cache."""
    adapter = MemoryCacheAdapter(max_size=100)
    cache = AsyncCache(adapter=adapter, default_ttl=60, enabled=True)

    # Function to perform a cache operation
    async def cache_operation(op_id):
        fn_name = f"concurrent_func_{op_id}"
        result = f"result_{op_id}"

        # Set in cache
        await cache.set(fn_name, (), {}, result)

        # Get from cache
        hit_, value_ = await cache.get(fn_name, (), {})

        # Return hit status and value
        return hit_, value_

    # Launch multiple concurrent cache operations
    tasks = []
    for i in range(10):
        tasks.append(asyncio.create_task(cache_operation(i)))

    # Wait for all to complete
    results = await asyncio.gather(*tasks)

    # Verify all operations succeeded
    for i, (hit, value) in enumerate(results):
        assert hit is True
        assert value == f"result_{i}"


@pytest.mark.asyncio
async def test_serialization_error_handling(memory_cache):
    """Test that SerializationError is properly handled."""

    # Create a class that will cause SerializationError
    class ComplexObject:
        def __init__(self):
            self.circular_ref = None

    # Create circular reference
    obj = ComplexObject()
    obj.circular_ref = obj

    # Try to cache it - should fail gracefully
    success = await memory_cache.set("serialization_error", (), {}, obj)
    assert success is False

    # Check error doesn't affect subsequent operations
    success = await memory_cache.set("valid_entry", (), {}, "This should work")
    assert success is True

    hit, value = await memory_cache.get("valid_entry", (), {})
    assert hit is True
    assert value == "This should work"


# Tests for custom key generation functionality
@pytest.mark.asyncio
async def test_custom_key_function(memory_cache):
    """Test using a custom key function."""

    # Define a custom key function that uses specific kwargs
    def custom_key_fn(context: CacheKeyContext) -> str:
        # Only use specific kwargs for key generation
        fn_name = context["fn_name"]
        # Only use the 'important' kwarg for caching
        important_value = context["kwargs"].get("important", None)
        return f"{fn_name}:{important_value}"

    # Set cache with custom key function
    await memory_cache.set(
        "custom_key_func", (), {"important": "value1", "ignore": "this"}, "result1", cache_key_fn=custom_key_fn
    )

    # Should hit cache even with different 'ignore' value
    hit, value = await memory_cache.get(
        "custom_key_func", (), {"important": "value1", "ignore": "different"}, cache_key_fn=custom_key_fn
    )

    assert hit is True
    assert value == "result1"

    # Should miss cache with different 'important' value
    hit, value = await memory_cache.get(
        "custom_key_func", (), {"important": "value2", "ignore": "this"}, cache_key_fn=custom_key_fn
    )

    assert hit is False


@pytest.mark.asyncio
async def test_entry_id_mapping(memory_cache):
    """Test mapping between entry_id and cache key."""

    # Set cache with entry_id
    entry_id = "test-entry-123"
    await memory_cache.set("mapped_func", (1, 2), {"a": "b"}, "result", entry_id=entry_id)

    # Check if we can retrieve the key
    key = await memory_cache.get_cache_key_for_id(entry_id)
    assert key is not None

    # Verify the key is correct by direct adapter access
    hit, value = await memory_cache.adapter.get(key)
    assert hit is True
    assert value == "result"


@pytest.mark.asyncio
async def test_invalidate_by_id(memory_cache):
    """Test invalidating cache by entry_id."""

    # Set cache with entry_id
    entry_id = "entry-to-invalidate"
    await memory_cache.set("invalidate_func", (), {}, "should be invalidated", entry_id=entry_id)

    # Verify it's cached
    hit, _ = await memory_cache.get("invalidate_func", (), {})
    assert hit is True

    # Invalidate by entry_id
    success = await memory_cache.invalidate_by_id(entry_id)
    assert success is True

    # Verify it's gone
    hit, _ = await memory_cache.get("invalidate_func", (), {})
    assert hit is False

    # Key mapping should be removed
    key = await memory_cache.get_cache_key_for_id(entry_id)
    assert key is None


@pytest.mark.asyncio
async def test_metadata_in_key_generation(memory_cache):
    """Test using metadata in custom key generation."""

    # Define a custom key function that uses metadata
    def metadata_key_fn(context: CacheKeyContext) -> str:
        fn_name = context["fn_name"]
        # Extract version from metadata
        metadata = context.get("metadata", {})
        version = metadata.get("version", "unknown")
        return f"{fn_name}:v{version}"

    # Set cache with metadata and custom key function
    await memory_cache.set(
        "versioned_func", (), {}, "v1-result", metadata={"version": "1.0"}, cache_key_fn=metadata_key_fn
    )

    # Set another entry with different version
    await memory_cache.set(
        "versioned_func", (), {}, "v2-result", metadata={"version": "2.0"}, cache_key_fn=metadata_key_fn
    )

    # Retrieve with v1
    hit, v1_value = await memory_cache.get(
        "versioned_func", (), {}, metadata={"version": "1.0"}, cache_key_fn=metadata_key_fn
    )

    # Retrieve with v2
    hit2, v2_value = await memory_cache.get(
        "versioned_func", (), {}, metadata={"version": "2.0"}, cache_key_fn=metadata_key_fn
    )

    # Should have different results for different versions
    assert hit is True and hit2 is True
    assert v1_value == "v1-result"
    assert v2_value == "v2-result"


@pytest.mark.asyncio
async def test_compose_key_functions(memory_cache):
    """Test composing multiple key functions together."""

    # Define component key functions
    def func_key(context: CacheKeyContext) -> str:
        return context["fn_name"]

    def user_id_key(context: CacheKeyContext) -> str:
        return str(context["kwargs"].get("user_id", "anon"))

    def version_key(context: CacheKeyContext) -> str:
        return context.get("metadata", {}).get("version", "1.0")

    # Compose the functions
    composite_key = compose_key_functions(func_key, user_id_key, version_key)

    # Test with the composite key function
    await memory_cache.set(
        "user_data", (), {"user_id": 123}, "user 123 data", metadata={"version": "2.0"}, cache_key_fn=composite_key
    )

    # Should hit with same parameters
    hit, value = await memory_cache.get(
        "user_data", (), {"user_id": 123}, metadata={"version": "2.0"}, cache_key_fn=composite_key
    )
    assert hit is True
    assert value == "user 123 data"

    # Should miss with different user_id
    hit, _ = await memory_cache.get(
        "user_data", (), {"user_id": 456}, metadata={"version": "2.0"}, cache_key_fn=composite_key
    )
    assert hit is False

    # Should miss with different version
    hit, _ = await memory_cache.get(
        "user_data", (), {"user_id": 123}, metadata={"version": "3.0"}, cache_key_fn=composite_key
    )
    assert hit is False

    # Verify the actual key format
    key = composite_key({"fn_name": "user_data", "kwargs": {"user_id": 123}, "metadata": {"version": "2.0"}})
    assert key == "user_data:123:2.0"


@pytest.mark.asyncio
async def test_extract_key_component(memory_cache):
    """Test using the extract_key_component utility."""

    # Create extractors for nested paths
    user_id_extractor = extract_key_component("kwargs.user_id")
    app_version = extract_key_component("metadata.app_version")

    # Create a composite key function
    composite_key = compose_key_functions(user_id_extractor, app_version)

    # Test with the composite key
    context = {
        "fn_name": "get_user",
        "kwargs": {"user_id": 42, "include_details": True},
        "metadata": {"app_version": "3.1.4"},
    }

    # Verify extraction works
    assert user_id_extractor(context) == "42"
    assert app_version(context) == "3.1.4"

    # Verify composite key
    assert composite_key(context) == "42:3.1.4"

    # Test with missing attributes
    empty_context = {"fn_name": "empty"}
    assert user_id_extractor(empty_context) == "none"
    assert app_version(empty_context) == "none"


@pytest.mark.asyncio
async def test_key_component_decorator(memory_cache):
    """Test using the key_component decorator."""

    @key_component("user")
    def user_key(context_: CacheKeyContext) -> str:
        return str(context_["kwargs"].get("user_id", "guest"))

    @key_component("action")
    def action_key(context_: CacheKeyContext) -> str:
        return context_["fn_name"]

    # Unnamed component (uses function name)
    @key_component()
    def timestamp(context_: CacheKeyContext) -> str:
        return context_.get("metadata", {}).get("timestamp", "0")

    # Create composite key
    composite_key = compose_key_functions(user_key, action_key, timestamp)

    # Test the key generation
    context = {"fn_name": "get_profile", "kwargs": {"user_id": 789}, "metadata": {"timestamp": "2023-04-01"}}

    assert user_key(context) == "user:789"
    assert action_key(context) == "action:get_profile"
    assert timestamp(context) == "timestamp:2023-04-01"
    assert composite_key(context) == "user:789:action:get_profile:timestamp:2023-04-01"


@pytest.mark.asyncio
async def test_temporarily_disabled(memory_cache):
    """Test the temporarily_disabled context manager."""

    # Set something in the cache
    await memory_cache.set("test_func", (), {}, "original value")

    # Verify it's there
    hit, value = await memory_cache.get("test_func", (), {})
    assert hit is True
    assert value == "original value"

    # Use the context manager to temporarily disable
    with memory_cache.temporarily_disabled():
        # This set should be skipped
        await memory_cache.set("test_func", (), {}, "new value")

        # Cache operations should return miss during this block
        hit, _ = await memory_cache.get("test_func", (), {})
        assert hit is False

    # After the block, cache should be enabled again
    hit, value = await memory_cache.get("test_func", (), {})
    assert hit is True
    # Should still have the original value
    assert value == "original value"


@pytest.mark.asyncio
async def test_key_validation(memory_cache):
    """Test key validation functionality."""

    # Enable validation for this test
    memory_cache.validate_keys = True
    memory_cache._generated_keys = set()

    # Define a function that returns invalid keys
    # noinspection PyUnusedLocal
    def invalid_key_fn(context: CacheKeyContext) -> str:
        # Force a type error by returning None which will be caught during validation
        return None  # type: ignore

    # Test with invalid key function
    with pytest.raises(InvalidCacheKeyError):
        await memory_cache.set("invalid_key_test", (), {}, "test", cache_key_fn=invalid_key_fn)

    # Define a function for duplicate keys
    # noinspection PyUnusedLocal
    def constant_key_fn(context: CacheKeyContext) -> str:
        return "same-key-always"

    # First use should work
    await memory_cache.set("duplicate_test_1", (), {}, "first value", cache_key_fn=constant_key_fn)

    # Should log warning about duplicate but still work
    await memory_cache.set("duplicate_test_2", (), {}, "second value", cache_key_fn=constant_key_fn)

    # Verify the latest value is used
    hit, value = await memory_cache.get("duplicate_test_2", (), {}, cache_key_fn=constant_key_fn)
    assert hit is True
    assert value == "second value"


@pytest.mark.asyncio
async def test_get_id_key_mapping(memory_cache):
    """Test the bulk entry ID key mapping functionality."""

    # Add several entries with entry IDs
    await memory_cache.set("func1", (), {}, "result1", entry_id="entry1")
    await memory_cache.set("func2", (), {}, "result2", entry_id="entry2")
    await memory_cache.set("func3", (), {}, "result3", entry_id="entry3")

    # Get the bulk mapping
    mapping = await memory_cache.get_id_key_mapping()

    # Should have 3 entries
    assert len(mapping) == 3
    assert "entry1" in mapping
    assert "entry2" in mapping
    assert "entry3" in mapping

    # The mapping should be a copy, so modifying it shouldn't affect the original
    mapping["new_entry"] = "new_key"
    new_key = await memory_cache.get_cache_key_for_id("new_entry")
    assert new_key is None

    # Invalidating should remove from the mapping
    await memory_cache.invalidate_by_id("entry1")
    entry1_key = await memory_cache.get_cache_key_for_id("entry1")
    assert entry1_key is None

    # But the original copy we made should be unaffected
    assert "entry1" in mapping


@pytest.mark.asyncio
async def test_cleanup_task():
    """Test the automatic cleanup of stale entry ID mappings."""

    # Create a cache with a very short cleanup interval
    adapter = MemoryCacheAdapter(max_size=10)
    cache = AsyncCache(adapter=adapter, default_ttl=1, enabled=True, cleanup_interval=1)

    try:
        # Start the cleanup task
        await cache.start_cleanup_task()

        # Set cache entries with entry IDs
        await cache.set("func1", (), {}, "result1", entry_id="entry1")
        await cache.set("func2", (), {}, "result2", entry_id="entry2")

        # Verify entry key mapping exists
        mapping = await cache.get_id_key_mapping()
        assert len(mapping) == 2
        assert "entry1" in mapping
        assert "entry2" in mapping

        # Wait for TTL to expire
        await asyncio.sleep(1.1)

        # Force a cache get to trigger TTL check in adapter
        hit1, _ = await cache.get("func1", (), {})
        hit2, _ = await cache.get("func2", (), {})
        assert hit1 is False
        assert hit2 is False

        # Wait for cleanup to run
        await asyncio.sleep(1.1)

        # Verify entry key mappings were cleaned up
        mapping = await cache.get_id_key_mapping()
        assert len(mapping) == 0

    finally:
        # Always stop the cleanup task
        await cache.stop_cleanup_task()


@pytest.mark.asyncio
async def test_manual_cleanup():
    """Test manual cleanup of stale entry ID mappings."""

    # Create a cache with cleanup disabled
    adapter = MemoryCacheAdapter(max_size=10)
    cache = AsyncCache(adapter=adapter, default_ttl=1, enabled=True, cleanup_interval=0)

    # Set cache entries with entry IDs
    await cache.set("func1", (), {}, "result1", entry_id="entry1")
    await cache.set("func2", (), {}, "result2", entry_id="entry2")

    # Verify entry key mapping exists
    mapping = await cache.get_id_key_mapping()
    assert len(mapping) == 2

    # Wait for TTL to expire
    await asyncio.sleep(1.1)

    # Force a cache get to trigger TTL check in adapter
    hit1, _ = await cache.get("func1", (), {})
    hit2, _ = await cache.get("func2", (), {})
    assert hit1 is False
    assert hit2 is False

    # Entry key mappings should still exist
    mapping = await cache.get_id_key_mapping()
    assert len(mapping) == 2

    # Manually run the cleanup
    await cache._cleanup_stale_mappings()

    # Verify entry key mappings were cleaned up
    mapping = await cache.get_id_key_mapping()
    assert len(mapping) == 0


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
