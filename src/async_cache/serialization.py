"""
Serialization utilities for the async_cache package.

Provides efficient serialization with support for complex Python types.
"""

import datetime
import types
import uuid
from typing import Any, Dict

import msgpack

from async_cache.exceptions import SerializationError


class MsgPackSerializer:
    """Handler for msgpack serialization and deserialization with custom type support."""

    # Type codes for custom types
    TYPE_DATETIME = 1
    TYPE_CUSTOM_OBJECT = 2
    TYPE_UUID = 3
    TYPE_STRING_FALLBACK = 99  # Special code for string fallback representations

    @staticmethod
    def encode(obj: Any, fallback: bool = False) -> bytes:
        """
        Serialize an object to bytes using msgpack.

        Args:
            obj: The object to serialize
            fallback: Whether to fallback to string representation for unsupported types
                      IMPORTANT: If True, may not roundtrip correctly

        Returns:
            Serialized bytes

        Raises:
            SerializationError: If serialization fails and fallback is False
        """
        try:
            return msgpack.packb(
                obj,
                default=lambda o: MsgPackSerializer._encode_hook(o, fallback),
                use_bin_type=True
            )
        except Exception as e:
            raise SerializationError(f"Failed to serialize object: {str(e)}")

    @staticmethod
    def decode(data: bytes) -> Any:
        """
        Deserialize bytes back to an object.

        Args:
            data: The serialized data

        Returns:
            Deserialized object

        Raises:
            SerializationError: If deserialization fails
        """
        try:
            return msgpack.unpackb(data, object_hook=MsgPackSerializer._decode_hook, raw=False)
        except Exception as e:
            raise SerializationError(f"Failed to deserialize data: {str(e)}")

    @staticmethod
    def _encode_hook(obj: Any, fallback: bool = False) -> Any:
        """
        Custom encoder for handling Python types not natively supported by msgpack.

        Note: When fallback is True, objects that can't be properly serialized
        will be converted to a special string representation format that includes
        the original type. These won't roundtrip to their original type.
        """
        # Handle datetime objects - fully supported for roundtrip
        if isinstance(obj, datetime.datetime):
            return {
                "__type_code": MsgPackSerializer.TYPE_DATETIME,
                "data": obj.isoformat()
            }

        # Handle UUID objects - fully supported for roundtrip
        if isinstance(obj, uuid.UUID):
            return {
                "__type_code": MsgPackSerializer.TYPE_UUID,
                "data": str(obj)
            }

        # Explicitly handle functions (including lambdas)
        if isinstance(obj, types.FunctionType):
            if fallback:
                return {
                    "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                    "type": type(obj).__name__,
                    "data": str(obj)
                }
            else:
                raise SerializationError(f"Functions of type {type(obj).__name__} are not serializable")

        # Handle objects with __dict__
        if hasattr(obj, "__dict__"):
            try:
                # Ensure the __dict__ is serializable
                serializable_dict = {}
                for key, value in obj.__dict__.items():
                    try:
                        # Test each value for serializability
                        msgpack.packb(
                            value,
                            default=lambda o: MsgPackSerializer._encode_hook(o, fallback=False),
                            use_bin_type=True
                        )
                        serializable_dict[key] = value
                    except Exception:
                        if fallback:
                            # If value can't be serialized, use string representation with type info
                            serializable_dict[key] = {
                                "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                                "type": type(value).__name__,
                                "data": str(value)
                            }
                        else:
                            # Without fallback, propagate the exception
                            raise

                return {
                    "__type_code": MsgPackSerializer.TYPE_CUSTOM_OBJECT,
                    "type": obj.__class__.__name__,
                    "module": obj.__class__.__module__,
                    "data": serializable_dict
                }
            except Exception as e:
                if fallback:
                    return {
                        "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                        "type": type(obj).__name__,
                        "data": str(obj)
                    }
                raise SerializationError(f"Object of type {type(obj).__name__} is not serializable: {str(e)}")

        # Handle objects with __slots__
        if hasattr(obj, "__slots__"):
            try:
                slot_dict = {}
                for slot in obj.__slots__:
                    if hasattr(obj, slot):
                        value = getattr(obj, slot)
                        try:
                            # Test each value for serializability
                            msgpack.packb(
                                value,
                                default=lambda o: MsgPackSerializer._encode_hook(o, fallback=False),
                                use_bin_type=True
                            )
                            slot_dict[slot] = value
                        except Exception:
                            if fallback:
                                # If value can't be serialized, use string representation with type info
                                slot_dict[slot] = {
                                    "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                                    "type": type(value).__name__,
                                    "data": str(value)
                                }
                            else:
                                # Without fallback, propagate the exception
                                raise

                return {
                    "__type_code": MsgPackSerializer.TYPE_CUSTOM_OBJECT,
                    "type": obj.__class__.__name__,
                    "module": obj.__class__.__module__,
                    "data": slot_dict
                }
            except Exception as e:
                if fallback:
                    return {
                        "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                        "type": type(obj).__name__,
                        "data": str(obj)
                    }
                raise SerializationError(f"Object of type {type(obj).__name__} is not serializable: {str(e)}")

        # As a last resort, if fallback is enabled, convert to string but preserve type info
        if fallback:
            return {
                "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                "type": type(obj).__name__,
                "data": str(obj)
            }

        # If we reach here without returning and fallback is False, raise an error
        raise SerializationError(f"Object of type {type(obj).__name__} is not serializable")

    @staticmethod
    def _decode_hook(obj: Dict[str, Any]) -> Any:
        """Custom decoder for handling Python types not natively supported by msgpack."""
        # Only process dictionaries with a type code
        if not isinstance(obj, dict) or "__type_code" not in obj:
            return obj

        type_code = obj["__type_code"]

        # Handle datetime objects
        if type_code == MsgPackSerializer.TYPE_DATETIME:
            return datetime.datetime.fromisoformat(obj["data"])

        # Handle UUID objects
        if type_code == MsgPackSerializer.TYPE_UUID:
            return uuid.UUID(obj["data"])

        # Handle string fallback representations - return with clear type annotation
        if type_code == MsgPackSerializer.TYPE_STRING_FALLBACK:
            return {
                "__serialized_string_of_type": obj["type"],
                "value": obj["data"]
            }

        # Handle custom objects
        if type_code == MsgPackSerializer.TYPE_CUSTOM_OBJECT:
            return {
                "__type": obj["type"],
                "__module": obj["module"],
                **obj["data"]
            }

        return obj
