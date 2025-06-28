"""Background service for continuous file watching across multiple projects."""

import json
import signal
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from .watcher.handler import IndexingEventHandler
from .indexer_logging import get_logger

logger = get_logger()

try:
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False


class IndexingService:
    """Background service for continuous file watching across multiple projects."""
    
    def __init__(self, config_file: Optional[str] = None):
        if not WATCHDOG_AVAILABLE:
            raise ImportError("Watchdog not available. Install with: pip install watchdog")
        
        self.config_file = config_file or str(Path.home() / '.claude-indexer' / 'config.json')
        self.observers: Dict[str, Observer] = {}
        self.running = False
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def load_config(self) -> Dict[str, Any]:
        """Load service configuration from file."""
        try:
            config_path = Path(self.config_file)
            if config_path.exists():
                with open(config_path) as f:
                    return json.load(f)
            else:
                # Create default config
                default_config = {
                    "projects": [],
                    "settings": {
                        "debounce_seconds": 2.0,
                        "watch_patterns": ["*.py", "*.md"],
                        "ignore_patterns": [
                            "*.pyc", "__pycache__", ".git", ".venv", 
                            "node_modules", ".env", "*.log"
                        ],
                        "max_file_size": 1048576,  # 1MB
                        "enable_logging": True
                    }
                }
                
                # Ensure config directory exists
                config_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(config_path, 'w') as f:
                    json.dump(default_config, f, indent=2)
                
                logger.info(f"📝 Created default config at {config_path}")
                return default_config
                
        except Exception as e:
            logger.error(f"❌ Failed to load config: {e}")
            return {"projects": [], "settings": {}}
    
    def save_config(self, config: Dict[str, Any]) -> bool:
        """Save configuration to file."""
        try:
            config_path = Path(self.config_file)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to save config: {e}")
            return False
    
    def add_project(self, project_path: str, collection_name: str, 
                   settings: Optional[Dict[str, Any]] = None) -> bool:
        """Add a project to the watch list."""
        config = self.load_config()
        
        # Check if project already exists
        for project in config["projects"]:
            if project["path"] == project_path:
                logger.warning(f"⚠️  Project {project_path} already in watch list")
                return False
        
        # Add new project
        project_config = {
            "path": project_path,
            "collection": collection_name,
            "enabled": True,
            "settings": settings or {}
        }
        
        config["projects"].append(project_config)
        
        if self.save_config(config):
            logger.info(f"✅ Added project: {project_path} -> {collection_name}")
            
            # Start watching if service is running
            if self.running:
                self._start_project_watcher(project_config, config["settings"])
            
            return True
        
        return False
    
    def remove_project(self, project_path: str) -> bool:
        """Remove a project from the watch list."""
        config = self.load_config()
        
        # Find and remove project
        original_count = len(config["projects"])
        config["projects"] = [p for p in config["projects"] if p["path"] != project_path]
        
        if len(config["projects"]) < original_count:
            if self.save_config(config):
                logger.info(f"✅ Removed project: {project_path}")
                
                # Stop watching if service is running
                if self.running and project_path in self.observers:
                    self._stop_project_watcher(project_path)
                
                return True
        else:
            logger.warning(f"⚠️  Project {project_path} not found in watch list")
        
        return False
    
    def list_projects(self) -> List[Dict[str, Any]]:
        """List all projects in the watch list."""
        config = self.load_config()
        return config.get("projects", [])
    
    def start(self) -> bool:
        """Start the background service."""
        if self.running:
            logger.info("ℹ️  Service is already running")
            return True
        
        try:
            config = self.load_config()
            projects = config.get("projects", [])
            global_settings = config.get("settings", {})
            
            if not projects:
                logger.info("ℹ️  No projects configured for watching")
                return False
            
            logger.info(f"🚀 Starting indexing service for {len(projects)} projects...")
            
            # Start watchers for each project
            for project in projects:
                if project.get("enabled", True):
                    success = self._start_project_watcher(project, global_settings)
                    if not success:
                        logger.warning(f"⚠️  Failed to start watcher for {project['path']}")
            
            self.running = True
            logger.info(f"✅ Service started with {len(self.observers)} active watchers")
            
            # Keep service running
            try:
                while self.running:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            finally:
                self.stop()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start service: {e}")
            return False
    
    def stop(self) -> bool:
        """Stop the background service."""
        if not self.running:
            return True
        
        try:
            logger.info("🛑 Stopping indexing service...")
            
            # Stop all observers
            for project_path, observer in self.observers.items():
                observer.stop()
                observer.join(timeout=5)
                logger.info(f"   Stopped watcher for {project_path}")
            
            self.observers.clear()
            self.running = False
            
            logger.info("✅ Service stopped")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to stop service: {e}")
            return False
    
    def restart(self) -> bool:
        """Restart the service."""
        logger.info("🔄 Restarting service...")
        if self.running:
            self.stop()
        return self.start()
    
    def get_status(self) -> Dict[str, Any]:
        """Get service status."""
        config = self.load_config()
        
        watchers_status = {}
        for project_path, observer in self.observers.items():
            watchers_status[project_path] = {
                "running": observer.is_alive(),
                "watch_count": len(observer.watches) if hasattr(observer, 'watches') else 0
            }
        
        return {
            "running": self.running,
            "config_file": self.config_file,
            "total_projects": len(config.get("projects", [])),
            "active_watchers": len(self.observers),
            "watchers": watchers_status,
            "settings": config.get("settings", {})
        }
    
    def _start_project_watcher(self, project_config: Dict[str, Any], 
                              global_settings: Dict[str, Any]) -> bool:
        """Start watcher for a single project."""
        project_path = project_config["path"]
        collection_name = project_config["collection"]
        
        try:
            # Validate project path
            if not Path(project_path).exists():
                logger.error(f"❌ Project path does not exist: {project_path}")
                return False
            
            # Merge settings
            settings = {**global_settings, **project_config.get("settings", {})}
            debounce_seconds = settings.get("debounce_seconds", 2.0)
            
            # Create event handler
            event_handler = IndexingEventHandler(
                project_path=project_path,
                collection_name=collection_name,
                debounce_seconds=debounce_seconds,
                settings=settings
            )
            
            # Create and start observer
            observer = Observer()
            observer.schedule(event_handler, project_path, recursive=True)
            observer.start()
            
            self.observers[project_path] = observer
            logger.info(f"👁️  Watching: {project_path} -> {collection_name}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start watcher for {project_path}: {e}")
            return False
    
    def _stop_project_watcher(self, project_path: str) -> bool:
        """Stop watcher for a single project."""
        if project_path not in self.observers:
            return True
        
        try:
            observer = self.observers[project_path]
            observer.stop()
            observer.join(timeout=5)
            del self.observers[project_path]
            
            logger.info(f"⏹️  Stopped watching: {project_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to stop watcher for {project_path}: {e}")
            return False
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"\n📡 Received signal {signum}, shutting down gracefully...")
        self.running = False


def create_default_service_config(config_path: str) -> Dict[str, Any]:
    """Create a default service configuration file."""
    config = {
        "projects": [],
        "settings": {
            "debounce_seconds": 2.0,
            "watch_patterns": ["*.py", "*.md"],
            "ignore_patterns": [
                "*.pyc", "__pycache__", ".git", ".venv", 
                "node_modules", ".env", "*.log", ".DS_Store"
            ],
            "max_file_size": 1048576,  # 1MB
            "enable_logging": True,
            "log_level": "INFO"
        }
    }
    
    try:
        config_file = Path(config_path)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        logger.info(f"📝 Created default service config at {config_path}")
        return config
        
    except Exception as e:
        logger.error(f"❌ Failed to create config: {e}")
        return config