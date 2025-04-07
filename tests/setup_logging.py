import logging
import logging.config
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Union, List, Any

import yaml


class LoggerWriter:
    """
    Redirects print statements to the logging system.
    Useful for capturing output from third-party libraries.

    Attributes:
        _level_fn: Function to call with log messages (e.g., logger.info)
        _buf: Buffer for partially processed content
    """

    def __init__(self, level_fn):
        """
        Initialize the LoggerWriter.

        Args:
            level_fn: The logging function to use (e.g., logger.info)
        """
        self._level_fn = level_fn
        self._buf = ''

    def write(self, buf):
        """
        Write content to the logger.

        Args:
            buf: Content to be logged
        """
        # Remove empty lines
        for line in buf.rstrip().splitlines():
            if line.strip():
                self._level_fn(line.rstrip())

    def flush(self):
        """Flush the buffer (no-op for compatibility)"""
        pass

    @classmethod
    def isatty(cls):
        """
        Check if the output stream is a tty (always returns False).
        Added to handle uvicorn's terminal check.

        Returns:
            False, indicating this is not a tty
        """
        return False


def _convert_level(level: Union[int, str]) -> int:
    """
    Convert a string log level to its numeric value.

    Args:
        level: String log level (e.g., "DEBUG") or numeric level

    Returns:
        Numeric log level
    """
    if isinstance(level, str):
        return logging.getLevelName(level.upper())
    return level


def load_yaml_config(config_path: Path, log_to_file: bool = True, debug: bool = False) -> dict | None:
    """
    Load logging configuration from YAML file.

    Args:
        config_path: Path to the YAML configuration file
        log_to_file: Whether to enable file logging handlers
        debug: Whether to print debug information

    Returns:
        Dictionary containing the logging configuration if successful, None otherwise
    """
    logger = logging.getLogger(__name__)

    if debug:
        print(f"Attempting to load logging config from: {config_path}")

    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

            if debug:
                print(f"Successfully loaded YAML config from: {config_path}")

            # If file logging is disabled, remove file handlers
            if not log_to_file and config and "handlers" in config:
                if debug:
                    print("File logging is disabled, removing file handlers...")

                # Store handlers to remove
                handlers_to_remove = [
                    handler_name for handler_name, handler in config["handlers"].items()
                    if "filename" in handler
                ]

                if debug and handlers_to_remove:
                    print(f"Removing handlers: {', '.join(handlers_to_remove)}")

                # Remove file handlers
                for handler_name in handlers_to_remove:
                    del config["handlers"][handler_name]

                    # Remove the handler from all loggers
                    for logger_config in config.get("loggers", {}).values():
                        if "handlers" in logger_config and handler_name in logger_config["handlers"]:
                            logger_config["handlers"].remove(handler_name)

                    # Remove from root logger if present
                    if "root" in config and "handlers" in config["root"]:
                        if handler_name in config["root"]["handlers"]:
                            config["root"]["handlers"].remove(handler_name)

            # Create directories for file handlers if file logging is enabled
            elif config and "handlers" in config:
                for handler_name, handler in config["handlers"].items():
                    if "filename" in handler:
                        log_file = Path(handler["filename"])

                        if debug:
                            abs_path = log_file.absolute()
                            print(f"Handler '{handler_name}' file path: {abs_path}")
                            print(f"Parent directory: {log_file.parent}")

                        log_file.parent.mkdir(parents=True, exist_ok=True)

                        if debug:
                            print(f"Created/verified directory: {log_file.parent}")

            return config

    except FileNotFoundError:
        msg = f"Logging config file not found: {config_path}"
        logger.warning(msg)
        if debug:
            print(f"Error: {msg}")
        return None

    except yaml.YAMLError as e:
        msg = f"Invalid YAML in logging config: {e}"
        logger.warning(msg)
        if debug:
            print(f"Error: {msg}")
        return None

    except Exception as e:
        msg = f"Unexpected error loading logging config: {e}"
        logger.warning(msg)
        if debug:
            print(f"Error: {msg}")
        return None


def apply_namespace_levels(namespace_levels: Dict[str, Union[int, str]]):
    """
    Apply specific log levels to different logger namespaces.
    This is particularly useful for tests to get detailed logs from specific components.

    Args:
        namespace_levels: Dict of namespace prefixes and their log levels
            e.g. {"agentic_boostai": "DEBUG", "urllib3": "WARNING"}
    """
    # Convert string level names to their numeric values
    processed_levels = {
        k: _convert_level(v) for k, v in namespace_levels.items()
    }

    # Apply levels to each namespace
    for namespace, level in processed_levels.items():
        if namespace.lower() == "root":
            logging.getLogger().setLevel(level)
            continue

        logger = logging.getLogger(namespace)
        logger.setLevel(level)


@dataclass
class LoggingConfig:
    """Configuration class for logging setup to reduce parameter overload."""
    root_level: Union[int, str] = None
    namespace_levels: Optional[Dict[str, Union[int, str]]] = None
    redirect_stdout: bool = False
    format_str: str = None
    debug: bool = False
    log_to_file: bool = True
    logging_config_path: Optional[Path] = None
    log_output_path: Optional[Path] = None
    root_folder: Optional[Path] = None


def setup_logging(config: Union[LoggingConfig, Dict[str, Any]]) -> List[logging.Handler]:
    """
    Configure logging for the application.

    Uses logging.yaml if available, otherwise falls back to basic configuration.

    Args:
        config: LoggingConfig object or dictionary with configuration parameters

    Returns:
        List of configured handlers
    """
    # Convert dict to LoggingConfig if needed
    if isinstance(config, dict):
        config = LoggingConfig(**config)

    debug = config.debug

    if debug:
        print("Setting up logging configuration...")

    # Clear existing handlers to avoid duplicates when reconfiguring
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Set defaults
    root_level = config.root_level
    if root_level is None:
        root_level = logging.DEBUG if debug else logging.INFO
    else:
        root_level = _convert_level(root_level)

    format_str = config.format_str
    if format_str is None:
        format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    if debug:
        print(f"File logging is {'enabled' if config.log_to_file else 'disabled'}")

    # Try to use YAML configuration if available
    yaml_config_used = False
    if config.logging_config_path:
        if debug:
            print(f"Logging config path is set to: {config.logging_config_path}")

        # Handle both absolute and relative paths
        yaml_config_path = config.logging_config_path
        if config.root_folder and not config.logging_config_path.is_absolute():
            yaml_config_path = config.root_folder / config.logging_config_path

        if debug:
            print(f"Resolved config path: {yaml_config_path}")

        yaml_config = load_yaml_config(
            yaml_config_path,
            log_to_file=config.log_to_file,
            debug=debug
        )

        if yaml_config:
            # If log directory is specified and file logging is enabled
            if config.log_output_path and config.log_to_file:
                log_dir = Path(config.log_output_path)
                if not log_dir.is_absolute() and config.root_folder:
                    log_dir = config.root_folder / log_dir

                if debug:
                    print(f"Log output path is set to: {log_dir}")

                log_dir.mkdir(parents=True, exist_ok=True)

                if debug:
                    print(f"Created/verified log directory: {log_dir}")

            # Apply YAML configuration
            try:
                logging.config.dictConfig(yaml_config)
                yaml_config_used = True

                if debug:
                    print("Successfully applied YAML logging configuration")

                # Override root logger level if specified
                if root_level is not None:
                    logging.getLogger().setLevel(root_level)

                    if debug:
                        print(f"Root logger level overridden to: {logging.getLevelName(root_level)}")

                # Apply namespace-specific levels if provided
                if config.namespace_levels:
                    apply_namespace_levels(config.namespace_levels)

                    if debug:
                        print(f"Applied namespace-specific log levels: {config.namespace_levels}")

                logging.info("Logging configured using YAML configuration")
            except Exception as e:
                logging.warning(f"Failed to apply YAML logging configuration: {e}")
                if debug:
                    print(f"Error applying logging configuration: {str(e)}")
                yaml_config_used = False
    elif debug:
        print("No logging config path provided")

    # Fallback to basic configuration if YAML config is not available or fails
    if not yaml_config_used:
        if debug:
            print("Using basic logging configuration")

        # Create and configure handler
        handler = logging.StreamHandler()
        formatter = logging.Formatter(format_str)
        handler.setFormatter(formatter)

        # Configure root logger
        root_logger.setLevel(root_level)
        root_logger.addHandler(handler)

        # Apply namespace-specific levels if provided
        if config.namespace_levels:
            apply_namespace_levels(config.namespace_levels)
            if debug:
                print(f"Applied namespace-specific log levels: {config.namespace_levels}")

        logging.info("Logging configured using basic configuration")

    # Redirect stdout/stderr if requested
    if config.redirect_stdout:
        if debug:
            print("Redirecting stdout and stderr to logging system")
        sys.stdout = LoggerWriter(logging.getLogger().info)
        sys.stderr = LoggerWriter(logging.getLogger().error)

    return root_logger.handlers


def setup_test_logging(
        root_level: Union[int, str] = logging.INFO,
        namespace_levels: Optional[Dict[str, Union[int, str]]] = None
) -> logging.StreamHandler:
    """
    Configure logging for tests with configurable log levels per namespace.
    This is specifically intended for testing environments where you need
    to programmatically control log levels.

    Args:
        root_level: Log level for the root logger (default: INFO)
        namespace_levels: Dict of namespace prefixes and their log levels
            e.g. {"agentic_boostai": "DEBUG", "urllib3": "WARNING"}
            If None, defaults to {"agentic_boostai": "DEBUG"}

    Returns:
        The console handler that was created
    """
    if namespace_levels is None:
        namespace_levels = {"agentic_boostai": logging.DEBUG}

    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Convert string level to int if needed
    root_level = _convert_level(root_level)

    # Create and configure a dedicated console handler for tests
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)  # Handler should allow all messages through
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(formatter)

    # Set root logger level
    root_logger.setLevel(root_level)
    root_logger.addHandler(console_handler)

    # Apply namespace levels
    if namespace_levels:
        apply_namespace_levels(namespace_levels)

    return console_handler


# For backward compatibility
def setup_logging_legacy(**kwargs) -> Optional[logging.Handler]:
    """
    Legacy interface for setup_logging that maintains backward compatibility.

    Args:
        **kwargs: Keyword arguments to pass to the new setup_logging function

    Returns:
        The first handler from the root logger, or None if no handlers
    """
    handlers = setup_logging(LoggingConfig(**kwargs))
    return handlers[0] if handlers else None
