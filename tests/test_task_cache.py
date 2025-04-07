"""
Test Suite for Task Cache with Msgpack

Tests the enhanced task cache implementation with msgpack serialization.
"""

import asyncio
import datetime
import math
import uuid
from typing import NamedTuple

import pytest

from async_task_worker.task_cache import MemoryCacheAdapter, TaskCache


# Test fixture for the cache
@pytest.fixture
async def memory_cache():
    """Provides a memory cache adapter for testing."""
    adapter = MemoryCacheAdapter(max_size=10)
    cache = TaskCache(adapter=adapter, default_ttl=60, enabled=True)
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
    func_name = "test_func"
    args = (1, 2, "test")
    kwargs = {"a": 1, "b": "test"}
    result = {"result": "value", "count": 42}

    # Set in cache
    success = await memory_cache.set(func_name, args, kwargs, result)
    assert success is True

    # Get from cache
    hit, cached_value = await memory_cache.get(func_name, args, kwargs)
    assert hit is True
    assert cached_value == result

    # Invalidate cache entry
    success = await memory_cache.invalidate(func_name, args, kwargs)
    assert success is True

    # Should be gone now
    hit, _ = await memory_cache.get(func_name, args, kwargs)
    assert hit is False


@pytest.mark.asyncio
async def test_key_generation_consistency(memory_cache):
    """Test that cache key generation is consistent for the same inputs."""
    func_name = "test_func"
    args = (1, 2, "test")
    kwargs = {"a": 1, "b": "test"}

    # Generate key multiple times
    key1 = memory_cache.generate_key(func_name, args, kwargs)
    key2 = memory_cache.generate_key(func_name, args, kwargs)

    # Keys should be identical for identical inputs
    assert key1 == key2

    # Different argument order in kwargs shouldn't matter
    kwargs_reordered = {"b": "test", "a": 1}
    key3 = memory_cache.generate_key(func_name, args, kwargs_reordered)
    assert key1 == key3


@pytest.mark.asyncio
async def test_cache_with_complex_types(memory_cache):
    """Test caching with more complex data types using msgpack."""
    func_name = "complex_func"

    # Test with various complex types
    args = ()
    kwargs = {
        "simple_object": SimpleObject(42),
        "slot_object": SlotObject(43),
        "tuple_object": CustomTuple(id="test", value=44),
        "datetime": datetime.datetime.now(),
        "uuid": uuid.uuid4(),
        "nested": {
            "list": [1, 2, {"a": 3}],
            "dict": {"key": [4, 5, 6]}
        }
    }

    result = {"status": "success", "data": kwargs}

    # Try to cache this complex data
    success = await memory_cache.set(func_name, args, kwargs, result)
    assert success is True

    # Retrieve it back
    hit, cached_result = await memory_cache.get(func_name, args, kwargs)
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

    func_name = "ttl_func"
    args = ()
    kwargs = {}
    result = {"result": "This should expire quickly"}

    # Set in cache
    await memory_cache.set(func_name, args, kwargs, result)

    # Should be available immediately
    hit, _ = await memory_cache.get(func_name, args, kwargs)
    assert hit is True

    # Wait for expiration
    await asyncio.sleep(0.15)

    # Should be gone now
    hit, _ = await memory_cache.get(func_name, args, kwargs)
    assert hit is False


@pytest.mark.asyncio
async def test_cache_max_size(memory_cache):
    """Test that cache evicts items when reaching max size."""
    # Cache adapter has max_size=10 from fixture

    # Add 15 items (should cause 5 evictions)
    for i in range(15):
        func_name = f"func_{i}"
        await memory_cache.set(func_name, (), {}, {"index": i})

    # Check how many items we can still retrieve
    # The first 5 should be evicted, and 10 should remain
    hit_count = 0
    for i in range(15):
        func_name = f"func_{i}"
        hit, _ = await memory_cache.get(func_name, (), {})
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
        func_name = f"func_{i}"
        await memory_cache.set(func_name, (), {}, {"index": i})

    # Verify they're in the cache
    hit, _ = await memory_cache.get("func_0", (), {})
    assert hit is True

    # Clear the cache
    await memory_cache.clear()

    # Verify everything is gone
    for i in range(5):
        func_name = f"func_{i}"
        hit, _ = await memory_cache.get(func_name, (), {})
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
    func_name = "order_test"
    args = (1, 2, 3)

    # Two identical kwargs dictionaries with different order
    kwargs1 = {"a": 1, "b": 2, "c": 3}
    kwargs2 = {"c": 3, "a": 1, "b": 2}

    # Cache with the first order
    result = {"result": "test"}
    await memory_cache.set(func_name, args, kwargs1, result)

    # Retrieve with the second order
    hit, cached_result = await memory_cache.get(func_name, args, kwargs2)

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

    # With fallback enabled (default), this should work
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

    func_name = "disabled_test"
    result = {"status": "success"}

    # Try to set in the cache
    success = await memory_cache.set(func_name, (), {}, result)
    assert success is False

    # Try to get from the cache
    hit, _ = await memory_cache.get(func_name, (), {})
    assert hit is False


@pytest.mark.asyncio
async def test_cache_error_handling(memory_cache):
    """Test error handling during cache operations."""
    # Create a faulty adapter method to simulate errors
    original_get = memory_cache.adapter.get

    # Replace with a method that raises an exception
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
    custom_ttl = 0.1  # 100ms
    await memory_cache.set("short_ttl", (), {}, "should expire quickly", ttl=custom_ttl)

    # Store another with default TTL
    await memory_cache.set("default_ttl", (), {}, "should last longer")

    # Check that both are initially available
    hit1, _ = await memory_cache.get("short_ttl", (), {})
    hit2, _ = await memory_cache.get("default_ttl", (), {})
    assert hit1 is True
    assert hit2 is True

    # Wait for the custom TTL to expire
    await asyncio.sleep(0.15)

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
    special_floats = {
        "inf": float("inf"),
        "neg_inf": float("-inf"),
        "nan": float("nan")
    }

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
    cache = TaskCache(adapter=adapter, default_ttl=60, enabled=True)

    # Function to perform a cache operation
    async def cache_operation(op_id):
        func_name = f"concurrent_func_{op_id}"
        result = f"result_{op_id}"

        # Set in cache
        await cache.set(func_name, (), {}, result)

        # Get from cache
        hit, value = await cache.get(func_name, (), {})

        # Return hit status and value
        return hit, value

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


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
