# 🚀 Unified Logging Architecture Plan - Enhanced Singleton Implementation

## Executive Summary

**Approach:** Enhanced Singleton with dictConfig (Option 1)
**Timeline:** 6 hours total implementation + testing
**Risk Level:** LOW - Zero breaking changes
**Components Affected:** 27 files (all continue working unchanged)

This plan modernizes the logging system while maintaining 100% backward compatibility across all components.

## Current Architecture Analysis

### Working System Features

- **✅ 27 components** using consistent `get_logger()` interface
- **✅ Singleton pattern** with global `_logger` variable for unified logging
- **✅ Collection-specific logs** at `{project_path}/logs/{collection_name}.log`
- **✅ loguru/stdlib dual support** with automatic feature detection
- **✅ File rotation** built-in (10MB files, 7-day retention)
- **✅ Entry points** properly call `setup_logging()` in CLI and main functions

### Affected Components

```
CLI: cli_full.py, cli.py, main.py
Core: indexer.py, service.py
Watcher: watcher/handler.py
Config: config_loader.py, project_config.py, models.py, legacy.py
Analysis: parser.py, entities.py, observation_extractor.py, javascript_parser.py
Storage: qdrant.py
```

### Component Usage Patterns

All components follow the same pattern:
```python
from claude_indexer.indexer_logging import get_logger
logger = get_logger()
logger.info("Component message")
```

## Implementation Strategy - Enhanced Singleton

**Core Principle:** Keep existing `get_logger()` interface, modernize underneath with dictConfig.

### Phase 1: Core Logging Infrastructure (2 hours)

#### File: `claude_indexer/logging_config.py` (NEW)

```python
"""Centralized logging configuration using dictConfig."""
import logging.config
from pathlib import Path
from typing import Optional

# Modern dictConfig-based configuration
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "detailed": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S"
        },
        "simple": {
            "format": "%(levelname)s | %(message)s"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "level": "INFO",
            "stream": "ext://sys.stderr"
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "detailed",
            "level": "DEBUG",
            "filename": "logs/claude-indexer.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 7
        }
    },
    "loggers": {
        "claude_indexer": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False
        }
    }
}

class LoggerManager:
    """Singleton logger manager with dictConfig."""
    _instance = None
    _configured = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def configure(self,
                 level: str = "INFO",
                 quiet: bool = False,
                 verbose: bool = False,
                 log_file: Optional[Path] = None,
                 collection_name: Optional[str] = None,
                 project_path: Optional[Path] = None):
        """Configure logging system once."""
        if self._configured:
            return

        # Dynamic configuration based on parameters
        config = self._build_config(level, quiet, verbose, log_file, collection_name, project_path)
        logging.config.dictConfig(config)
        self._configured = True

    def _build_config(self, level, quiet, verbose, log_file, collection_name, project_path):
        """Build dynamic configuration."""
        config = LOGGING_CONFIG.copy()

        # Determine effective level
        if quiet:
            effective_level = "ERROR"
        elif verbose:
            effective_level = "DEBUG"
        else:
            effective_level = level

        # Update console handler
        if quiet:
            del config["handlers"]["console"]
            config["loggers"]["claude_indexer"]["handlers"] = ["file"]
        else:
            config["handlers"]["console"]["level"] = effective_level

        # Update file handler with collection-specific path
        if log_file:
            config["handlers"]["file"]["filename"] = str(log_file)
        elif collection_name:
            # Use existing get_default_log_file logic
            from .indexer_logging import get_default_log_file
            default_file = get_default_log_file(collection_name, project_path)
            if default_file:
                config["handlers"]["file"]["filename"] = str(default_file)

        return config

    def get_logger(self, name: str = "claude_indexer"):
        """Get configured logger."""
        self.configure()  # Ensure configured
        return logging.getLogger(name)

# Global instance
_logger_manager = LoggerManager()
```

#### File: `claude_indexer/indexer_logging.py` (MODIFIED)

```python
"""Legacy-compatible logging interface."""
from pathlib import Path
from typing import Optional
from .logging_config import _logger_manager

# Backward compatibility - keep existing functions
def setup_logging(
    level: str = "INFO",
    quiet: bool = False,
    verbose: bool = False,
    log_file: Path | None = None,
    enable_file_logging: bool = True,
    collection_name: str | None = None,
    project_path: Path | None = None,
):
    """Setup global logging configuration - backward compatible."""
    _logger_manager.configure(level, quiet, verbose, log_file, collection_name, project_path)
    return _logger_manager.get_logger()

def get_logger():
    """Get the global logger instance - backward compatible."""
    return _logger_manager.get_logger()

# Keep existing utility functions
def get_default_log_file(collection_name: str | None = None, project_path: Path | None = None) -> Path | None:
    """Existing log file path logic - unchanged."""
    # ... existing implementation preserved ...
    pass
```

### Phase 2: Component Integration (1 hour)

**No Changes Needed** - All existing components continue using:
```python
from claude_indexer.indexer_logging import get_logger
logger = get_logger()
```

**Entry Points Update** - Ensure `setup_logging()` calls in:
- `claude_indexer/cli_full.py` - CLI commands
- `claude_indexer/main.py` - Main functions
- `claude_indexer/service.py` - Service startup

### Phase 3: Testing Strategy (2 hours)

#### Test File: `debug/test_unified_logging.py`

```python
"""Test unified logging across all components."""
import pytest
import tempfile
import logging
from pathlib import Path

def test_all_components_use_same_logger():
    """Verify all components get same logger instance."""
    from claude_indexer.indexer_logging import get_logger
    from claude_indexer.indexer import CoreIndexer
    from claude_indexer.service import IndexingService
    from claude_indexer.watcher.handler import IndexingEventHandler

    # Reset logging state
    logging.getLogger().handlers.clear()

    # All should return same logger
    logger1 = get_logger()
    logger2 = get_logger()
    assert logger1 is logger2

def test_component_logging_integration():
    """Test logging works across all major components."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"

        # Setup logging
        from claude_indexer.indexer_logging import setup_logging
        setup_logging(level="DEBUG", log_file=log_file, verbose=True)

        # Test each component logs correctly
        from claude_indexer.indexer_logging import get_logger
        logger = get_logger()

        logger.info("Test message from unified system")
        assert log_file.exists()

        content = log_file.read_text()
        assert "Test message from unified system" in content

def test_configuration_isolation():
    """Test different configurations don't interfere."""
    # Test collection-specific logging
    with tempfile.TemporaryDirectory() as tmpdir:
        from claude_indexer.indexer_logging import setup_logging

        # Setup with collection name
        setup_logging(collection_name="test-collection", project_path=Path(tmpdir))

        logger = get_logger()
        logger.info("Collection-specific test")

        # Verify log file created in expected location
        # ... implementation ...

def test_backward_compatibility():
    """Ensure existing component imports still work."""
    # Test CLI pattern
    from claude_indexer.indexer_logging import get_logger
    logger = get_logger()
    logger.info("CLI test")

    # Test indexer pattern
    from claude_indexer.indexer import CoreIndexer
    # Verify CoreIndexer can still get logger

    # Test watcher pattern
    from claude_indexer.watcher.handler import IndexingEventHandler
    # Verify watcher can still get logger

def test_quiet_mode():
    """Test quiet mode suppresses console output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "quiet_test.log"

        from claude_indexer.indexer_logging import setup_logging
        setup_logging(quiet=True, log_file=log_file)

        logger = get_logger()
        logger.info("This should only go to file")

        # Verify message in file but not console
        assert log_file.exists()
        content = log_file.read_text()
        assert "This should only go to file" in content

def test_verbose_mode():
    """Test verbose mode enables debug level."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "verbose_test.log"

        from claude_indexer.indexer_logging import setup_logging
        setup_logging(verbose=True, log_file=log_file)

        logger = get_logger()
        logger.debug("Debug message")

        # Verify debug message captured
        content = log_file.read_text()
        assert "Debug message" in content

def test_log_rotation():
    """Test log rotation configuration."""
    # Test that rotating file handler is configured correctly
    from claude_indexer.logging_config import LOGGING_CONFIG

    file_handler = LOGGING_CONFIG["handlers"]["file"]
    assert file_handler["class"] == "logging.handlers.RotatingFileHandler"
    assert file_handler["maxBytes"] == 10485760  # 10MB
    assert file_handler["backupCount"] == 7
```

#### Integration Tests

```bash
# Test CLI with new logging
claude-indexer -p debug/small_project -c test-logging --verbose

# Test watcher with new logging
claude-indexer watch start -p debug/small_project -c test-logging

# Test service with new logging
claude-indexer service start

# Test all major components
pytest debug/test_unified_logging.py -v

# Test existing functionality still works
pytest tests/unit/ -k logging
pytest tests/integration/ -k logging
```

### Phase 4: Production Deployment (1 hour)

#### Migration Checklist

- [ ] **Backup existing log files**
  ```bash
  cp -r logs/ logs_backup_$(date +%Y%m%d)
  ```

- [ ] **Test CLI commands with `--verbose` flag**
  ```bash
  claude-indexer -p . -c test --verbose
  ```

- [ ] **Test watcher startup and file events**
  ```bash
  claude-indexer watch start -p debug/test_project -c test
  ```

- [ ] **Test service management commands**
  ```bash
  claude-indexer service start
  claude-indexer service status --verbose
  ```

- [ ] **Verify log rotation working**
  ```bash
  ls -la logs/
  # Check file sizes and rotation
  ```

- [ ] **Check collection-specific log files**
  ```bash
  claude-indexer -p . -c collection1 --verbose
  claude-indexer -p . -c collection2 --verbose
  # Verify separate log files created
  ```

#### Rollback Plan

```bash
# Keep original file as backup
cp claude_indexer/indexer_logging.py claude_indexer/indexer_logging.backup.py

# Simple file replacement if issues found
mv claude_indexer/indexer_logging.backup.py claude_indexer/indexer_logging.py
rm claude_indexer/logging_config.py
```

## Benefits Analysis

### ✅ Advantages

- **Minimal Risk** - No import changes across 27 components
- **Modern Configuration** - dictConfig standard (2024 best practice)
- **Backward Compatible** - All existing `get_logger()` calls work
- **Centralized Control** - Single configuration source
- **Production Ready** - Proven patterns, no experimental features
- **File Rotation** - Built-in log rotation with size limits
- **Collection Isolation** - Separate log files per collection
- **Performance** - Standard logging performance, no overhead

### ⚠️ Considerations

- Still uses singleton pattern (but simplified and modernized)
- No component isolation (all use same logger name)
- One global configuration (but can be dynamic)
- Requires careful testing of all entry points

## MVP Scope - Essential Only

**Phase 1 Only:** Replace logging infrastructure, keep all interfaces
**Skip:** Component-specific loggers, async logging, performance optimization
**Focus:** Stable foundation with modern configuration

## Files Modified Summary

```
NEW:    claude_indexer/logging_config.py     (dictConfig setup)
MODIFY: claude_indexer/indexer_logging.py    (compatibility layer)
TEST:   debug/test_unified_logging.py        (comprehensive tests)
DOCS:   docs/logging.md                      (this documentation)
```

**Zero files need import changes** - All 27 components continue working unchanged.

## Architecture Comparison

### Before (Current)
```
Component → get_logger() → IndexerLogger class → loguru/stdlib → File/Console
```

### After (Enhanced)
```
Component → get_logger() → LoggerManager → dictConfig → RotatingFileHandler/StreamHandler
```

## Testing Coverage

### Unit Tests
- Logger singleton behavior
- Configuration parameter handling
- Quiet/verbose mode functionality
- File path resolution
- Backward compatibility

### Integration Tests
- CLI logging across all commands
- Watcher file event logging
- Service startup and management logging
- Collection-specific log file creation
- Log rotation behavior

### End-to-End Tests
- Full workflow with logging enabled
- Multiple component interaction logging
- Error handling and exception logging
- Performance impact measurement

## Implementation Timeline

| Phase | Duration | Activities |
|-------|----------|------------|
| 1 | 2 hours | Create logging_config.py, modify indexer_logging.py |
| 2 | 1 hour | Verify entry points, test basic functionality |
| 3 | 2 hours | Comprehensive testing, edge cases |
| 4 | 1 hour | Production deployment, verification |

**Total: 6 hours**

## Risk Mitigation

### Low Risk Factors
- ✅ No import changes required
- ✅ Existing interfaces preserved
- ✅ Standard library patterns used
- ✅ Comprehensive test coverage planned

### Mitigation Strategies
- Keep backup of original files
- Incremental testing at each phase
- Rollback plan documented
- Test in debug environment first

## Success Criteria

### Functional Requirements
- [ ] All 27 components continue logging without changes
- [ ] CLI commands log with correct verbosity levels
- [ ] Watcher events logged properly
- [ ] Service management logged correctly
- [ ] Collection-specific log files created
- [ ] Log rotation works as configured

### Non-Functional Requirements
- [ ] No performance degradation
- [ ] Memory usage remains stable
- [ ] Log file sizes managed properly
- [ ] Configuration is maintainable

## Future Enhancements (Post-MVP)

### Component-Specific Loggers
```python
# Future: Component isolation
logger = get_logger("indexer")
logger = get_logger("watcher")
logger = get_logger("service")
```

### Structured Logging
```python
# Future: JSON logging for monitoring
logger.info("File processed", extra={
    "file_path": path,
    "duration": elapsed,
    "entities_created": count
})
```

### Async Logging
```python
# Future: High-performance async logging
from logging.handlers import QueueHandler, QueueListener
```

---

**Implementation Status:** 📋 Plan Complete - Ready for Development

**Next Steps:** Begin Phase 1 implementation with `claude_indexer/logging_config.py`
