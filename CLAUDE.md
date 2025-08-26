# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Test Commands
- Setup environment: `uv venv && source .venv/bin/activate && uv sync --all-groups`
- Build: `hatch build`
- Run tests: `pytest`
- Run single test: `pytest tests/test_file.py::test_function_name`
- Skip integration tests: `pytest -m "not integration"` (default)
- Run only asyncio tests: `pytest -m asyncio`
- Never perform git add. I manage git manually.

## Architecture Overview

This is a multi-package async task worker system with three main packages:

### Core Packages
1. **async_task_worker**: Main task execution and management system
   - `AsyncTaskWorker`: Central coordinator integrating all components
   - `WorkerPool`: Manages worker lifecycle and task execution processing  
   - `TaskQueue`: Priority-based task queuing and retrieval
   - `TaskExecutor`: Individual task execution with timeout and caching
   - `TaskRegistry`: Global registry for task handlers with `@task` decorator
   - `AsyncCacheAdapter`: Integration with async_cache for result caching

2. **async_cache**: Flexible caching system with composable key generation
   - `AsyncCache`: Core cache with TTL and LRU eviction
   - `MemoryCacheAdapter`, `RedisCacheAdapter`: Storage backends
   - Key composition with `compose_key_functions` and `extract_key_component`
   - MsgPack serialization for efficient storage

3. **async_events**: Event management and SSE support  
   - `EventManager`: Pub/sub system with group-based filtering
   - `SSEClient`: Server-sent events client for real-time updates
   - Task state change events for reactive architectures

### Component Integration
- Tasks registered with `@task("task_type")` decorator go into global registry
- `AsyncTaskWorker` coordinates `WorkerPool`, `TaskQueue`, and `TaskExecutor`
- Optional caching via `AsyncCacheAdapter` wrapping `async_cache.AsyncCache`
- Event integration allows task state changes to publish events
- FastAPI router in `api.py` exposes REST endpoints for task management

### Key Patterns
- **Task Functions**: Must be async, can accept `progress_callback` parameter
- **Cache Key Generation**: Flexible, composable functions based on args/kwargs/metadata
- **Error Hierarchy**: Structured exceptions inheriting from `TaskError`
- **Resource Cleanup**: All async contexts use proper try/finally with cancellation handling
- **Worker Pool Architecture**: Decoupled design allowing independent scaling

## Code Style Guidelines
- Python 3.11+ with strict type annotations
- Imports: standard library first, third-party second, local modules third
- Error handling: Use structured hierarchy with `TaskError` base class
- Async patterns: Proper cancellation handling with try/finally and asyncio.timeout
- Naming: CamelCase for classes, snake_case for functions/methods, UPPER_SNAKE_CASE for constants
- Documentation: Docstrings with type annotations in code
- Testing: Use pytest fixtures with proper async teardown
- Clean code: maintain proper resource cleanup in all async contexts