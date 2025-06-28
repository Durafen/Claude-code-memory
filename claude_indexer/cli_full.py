"""Click-based CLI interface for the Claude Code indexer."""

import sys
import os
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from .config import load_config, IndexerConfig
from .indexer import CoreIndexer
from .embeddings.registry import create_embedder_from_config
from .storage.registry import create_store_from_config
from .indexer_logging import setup_logging, clear_log_file, get_logger

# Only import these if they're available
try:
    from .service import IndexingService
    from .git_hooks import GitHooksManager
    SERVICE_AVAILABLE = True
except ImportError:
    SERVICE_AVAILABLE = False

try:
    import click
    CLICK_AVAILABLE = True
except ImportError:
    CLICK_AVAILABLE = False

# Minimal CLI function for when Click is not available
def cli():
    """Claude Code Memory Indexer - Universal semantic indexing for codebases."""
    if not CLICK_AVAILABLE:
        from .indexer_logging import get_logger
        logger = get_logger()
        logger.error("Click not available. Install with: pip install click")
        sys.exit(1)

# Skip Click decorators and complex CLI setup when Click is not available
if not CLICK_AVAILABLE:
    # Early exit to prevent decorator errors during import
    import sys
    # Don't process the rest of the file to avoid decorator errors
    sys.modules[__name__].__dict__.update(locals())
    if __name__ == '__main__':
        cli()
        sys.exit(1)
else:
    # Only define Click-based CLI when Click is available

    # Common options as decorators
    def common_options(f):
        """Common options for indexing commands."""
        f = click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')(f)
        f = click.option('--quiet', '-q', is_flag=True, help='Suppress non-error output')(f)
        f = click.option('--config', type=click.Path(exists=True), help='Configuration file path')(f)
        return f


    def project_options(f):
        """Project-specific options."""
        f = click.option('--project', '-p', type=click.Path(), required=True, 
                        help='Project directory path')(f)
        f = click.option('--collection', '-c', required=True, 
                        help='Collection name for vector storage')(f)
        return f



    @click.group(invoke_without_command=True)
    @click.version_option(version="1.0.0")
    @click.pass_context
    def cli(ctx):
        """Claude Code Memory Indexer - Universal semantic indexing for codebases."""
        # If no subcommand, this will be handled by the default routing in wrapper
        pass

    @cli.command()
    @project_options
    @common_options
    @click.option('--include-tests', is_flag=True, help='Include test files in indexing')
    @click.option('--clear', is_flag=True, help='Clear code-indexed memories before indexing (preserves manual memories)')
    @click.option('--clear-all', is_flag=True, help='Clear ALL memories before indexing (including manual ones)')
    @click.option('--depth', type=click.Choice(['basic', 'full']), default='full',
                  help='Analysis depth')
    def index(project, collection, verbose, quiet, config, include_tests, 
            clear, clear_all, depth):
        """Index an entire project."""
        
        if quiet and verbose:
            click.echo("Error: --quiet and --verbose are mutually exclusive", err=True)
            sys.exit(1)
        
        try:
            # Setup logging with collection-specific file logging
            logger = setup_logging(quiet=quiet, verbose=verbose, collection_name=collection)
            
            # Load configuration
            config_obj = load_config(Path(config) if config else None)
        
            # Validate project path
            project_path = Path(project).resolve()
            if not project_path.exists():
                click.echo(f"Error: Project path does not exist: {project_path}", err=True)
                sys.exit(1)
            
            # Create components using direct Qdrant integration
            embedder = create_embedder_from_config({
                "provider": "openai",
                "api_key": config_obj.openai_api_key,
                "model": "text-embedding-3-small",
                "enable_caching": True
            })
            
            vector_store = create_store_from_config({
                "backend": "qdrant",
                "url": config_obj.qdrant_url,
                "api_key": config_obj.qdrant_api_key,
                "enable_caching": True
            })
            
            if not quiet and verbose:
                click.echo("⚡ Using Qdrant + OpenAI (direct mode)")
            
            # Create indexer
            indexer = CoreIndexer(config_obj, embedder, vector_store, project_path)
            
            # Clear collection if requested
            if clear or clear_all:
                if clear and clear_all:
                    click.echo("Error: --clear and --clear-all are mutually exclusive", err=True)
                    sys.exit(1)
                
                preserve_manual = not clear_all  # clear preserves manual, clear_all doesn't
                if not quiet:
                    if clear_all:
                        click.echo(f"🗑️ Clearing ALL memories in collection: {collection}")
                    else:
                        click.echo(f"🗑️ Clearing code-indexed memories in collection: {collection}")
                
                # Clear the log file for this collection
                log_cleared = clear_log_file(collection)
                if not quiet and log_cleared:
                    click.echo(f"🗑️ Cleared log file for collection: {collection}")
                
                success = indexer.clear_collection(collection, preserve_manual=preserve_manual)
                if not success:
                    click.echo("❌ Failed to clear collection", err=True)
                    sys.exit(1)
                elif not quiet:
                    if clear_all:
                        click.echo("✅ All memories cleared")
                    else:
                        click.echo("✅ Code-indexed memories cleared (manual memories preserved)")
                
                # Exit after clearing - don't auto-index
                return
        
            # Auto-detect incremental mode and run indexing only if not clearing
            state_file = indexer._get_state_file(collection)
            incremental = state_file.exists()
            
            if not quiet and verbose:
                click.echo(f"🔄 Indexing project: {project_path}")
                click.echo(f"📦 Collection: {collection}")
                if incremental:
                    click.echo("⚡ Mode: Incremental (auto-detected)")
                else:
                    click.echo("🔄 Mode: Full (auto-detected)")
            
            result = indexer.index_project(
                collection_name=collection,
                include_tests=include_tests
            )
        
        
            # Report results
            if result.success:
                if not quiet:
                    click.echo(f"✅ Indexing completed in {result.processing_time:.1f}s")
                    click.echo(f"   Files processed: {result.files_processed}")
                    click.echo(f"   Entities created: {result.entities_created}")
                    click.echo(f"   Relations created: {result.relations_created}")
                    
                    # Report cost information if available 
                    if result.total_tokens > 0:
                        click.echo("💰 OpenAI Usage:")
                        click.echo(f"   Tokens consumed: {result.total_tokens:,}")
                        if result.embedding_requests > 0:
                            click.echo(f"   API requests: {result.embedding_requests}")
                        if result.total_cost_estimate > 0:
                            # Format cost nicely based on amount
                            if result.total_cost_estimate < 0.01:
                                click.echo(f"   Estimated cost: ${result.total_cost_estimate:.6f}")
                            else:
                                click.echo(f"   Estimated cost: ${result.total_cost_estimate:.4f}")
                        
                        # Check pricing accuracy and show current model info
                        if hasattr(embedder, 'get_model_info'):
                            model_info = embedder.get_model_info()
                            model_name = model_info.get('model', 'unknown')
                            cost_per_1k = model_info.get('cost_per_1k_tokens', 0)
                            click.echo(f"   Model: {model_name} (${cost_per_1k:.5f}/1K tokens)")
                    
                    if result.warnings and verbose:
                        click.echo("⚠️  Warnings:")
                        for warning in result.warnings:
                            click.echo(f"   {warning}")
            else:
                click.echo("❌ Indexing failed", err=True)
                for error in result.errors:
                    click.echo(f"   {error}", err=True)
                sys.exit(1)
        
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)


    @cli.command()
    @project_options
    @common_options
    @click.argument('file_path', type=click.Path(exists=True))
    def file(project, collection, file_path, verbose, quiet, config):
        """Index a single file."""
        
        try:
            # Load configuration
            config_obj = load_config(Path(config) if config else None)
            
            # Validate paths
            project_path = Path(project).resolve()
            target_file = Path(file_path).resolve()
            
            # Ensure file is within project
            try:
                target_file.relative_to(project_path)
            except ValueError:
                click.echo(f"Error: File must be within project directory", err=True)
                sys.exit(1)
            
            # Create components
            embedder = create_embedder_from_config({
                "provider": "openai",
                "api_key": config_obj.openai_api_key,
                "model": "text-embedding-3-small"
            })
            
            vector_store = create_store_from_config({
                "backend": "qdrant",
                "url": config_obj.qdrant_url,
                "api_key": config_obj.qdrant_api_key
            })
            
            # Create indexer and process file
            indexer = CoreIndexer(config_obj, embedder, vector_store, project_path)
            
            if not quiet:
                click.echo(f"🔄 Indexing file: {target_file.relative_to(project_path)}")
            
            result = indexer.index_single_file(target_file, collection)
            
            # Report results
            if result.success:
                if not quiet:
                    click.echo(f"✅ File indexed in {result.processing_time:.1f}s")
                    click.echo(f"   Entities: {result.entities_created}")
                    click.echo(f"   Relations: {result.relations_created}")
            else:
                click.echo("❌ File indexing failed", err=True)
                for error in result.errors:
                    click.echo(f"   {error}", err=True)
                sys.exit(1)
        
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            sys.exit(1)


    @cli.group()
    def watch():
        """File watching commands."""
        pass


    @watch.command()
    @project_options
    @common_options
    @click.option('--debounce', type=float, default=2.0, 
                  help='Debounce delay in seconds (default: 2.0)')
    @click.pass_context
    def start(ctx, project, collection, verbose, quiet, config, debounce):
        """Start file watching for real-time indexing."""
        
        try:
            from .watcher.handler import IndexingEventHandler
            from watchdog.observers import Observer
            from .service import IndexingService
            
            # Validate project path first
            project_path = Path(project).resolve()
            if not project_path.exists():
                click.echo(f"Error: Project path does not exist: {project_path}", err=True)
                sys.exit(1)
            
            # Setup logging with project path
            logger = setup_logging(quiet=quiet, verbose=verbose, collection_name=collection, project_path=project_path)
            
            # Load configuration
            config_obj = load_config(Path(config) if config else None)
            
            # Load service configuration for watch patterns and settings
            service = IndexingService()
            service_config = service.load_config()
            service_settings = service_config.get("settings", {})
            
            # Determine effective debounce using proper configuration hierarchy
            # CLI override > JSON config > built-in default
            debounce_explicitly_set = 'debounce' in ctx.params and ctx.get_parameter_source('debounce') != click.core.ParameterSource.DEFAULT
            effective_debounce = debounce if debounce_explicitly_set else service_settings.get("debounce_seconds", 2.0)
            
            # Create event handler with service configuration
            settings = {
                "debounce_seconds": effective_debounce,
                "watch_patterns": service_settings.get("watch_patterns", ["*.py", "*.md"]),
                "ignore_patterns": service_settings.get("ignore_patterns", [
                    "*.pyc", "__pycache__", ".git", ".venv", 
                    "node_modules", ".env", "*.log"
                ]),
                "max_file_size": service_settings.get("max_file_size", 1048576),
                "enable_logging": service_settings.get("enable_logging", True)
            }
            
            event_handler = IndexingEventHandler(
                project_path=str(project_path),
                collection_name=collection,
                debounce_seconds=effective_debounce,
                settings=settings,
                verbose=verbose
            )
            
            # Run initial incremental indexing before starting file watching
            logger.info("🔄 Running initial incremental indexing...")
            
            from claude_indexer.main import run_indexing
            try:
                run_indexing(
                    project_path=str(project_path),
                    collection_name=collection,
                    quiet=quiet,
                    verbose=verbose
                )
                logger.info("✅ Initial indexing complete")
            except Exception as e:
                logger.warning(f"⚠️ Initial indexing failed: {e}")
                logger.info("📁 Continuing with file watching...")
            
            # Start observer
            observer = Observer()
            observer.schedule(event_handler, str(project_path), recursive=True)
            observer.start()
            
            logger.info(f"👁️  Watching: {project_path}")
            logger.info(f"📦 Collection: {collection}")
            logger.info(f"⏱️  Debounce: {effective_debounce}s")
            logger.info("Press Ctrl+C to stop")
            
            # Setup signal handling
            import signal
            def signal_handler(signum, frame):
                observer.stop()
                logger.info(f"\n🛑 Received signal {signum}, stopping file watcher...")
                raise KeyboardInterrupt()
            
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
            
            try:
                while True:
                    import time
                    time.sleep(1)
            except KeyboardInterrupt:
                observer.stop()
                observer.join(timeout=3)  # Add timeout
                if observer.is_alive():
                    logger.warning("⚠️ Force stopping watcher")
            
            logger.info("✅ File watcher stopped")
        
        except ImportError:
            click.echo("Error: Watchdog not available. Install with: pip install watchdog", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            sys.exit(1)


    @cli.group()
    def service():
        """Background service commands."""
        pass


    @service.command()
    @common_options
    @click.option('--config-file', type=click.Path(), 
                  help='Service configuration file path')
    def start(verbose, quiet, config, config_file):
        """Start the background indexing service."""
        
        try:
            svc = IndexingService(config_file)
            
            if not quiet:
                click.echo("🚀 Starting background indexing service...")
            
            success = svc.start()
            
            if not success:
                click.echo("❌ Failed to start service", err=True)
                sys.exit(1)
        
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            sys.exit(1)


    @service.command('add-project')
    @click.argument('project_path', type=click.Path(exists=True))
    @click.argument('collection_name')
    @common_options
    @click.option('--config-file', type=click.Path(), 
                  help='Service configuration file path')
    def add_project(project_path, collection_name, verbose, quiet, config, config_file):
        """Add a project to the service watch list."""
        
        try:
            svc = IndexingService(config_file)
            project_path = str(Path(project_path).resolve())
            
            success = svc.add_project(project_path, collection_name)
            
            if success:
                if not quiet:
                    click.echo(f"✅ Added project: {project_path} -> {collection_name}")
            else:
                click.echo("❌ Failed to add project", err=True)
                sys.exit(1)
        
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            sys.exit(1)


    @service.command()
    @common_options
    @click.option('--config-file', type=click.Path(), 
                  help='Service configuration file path')
    def status(verbose, quiet, config, config_file):
        """Show service status."""
        
        try:
            svc = IndexingService(config_file)
            status_info = svc.get_status()
            
            click.echo(f"Service Status: {'🟢 Running' if status_info['running'] else '🔴 Stopped'}")
            click.echo(f"Config file: {status_info['config_file']}")
            click.echo(f"Projects: {status_info['total_projects']}")
            click.echo(f"Active watchers: {status_info['active_watchers']}")
            
            if verbose and status_info['watchers']:
                click.echo("\nWatchers:")
                for project, info in status_info['watchers'].items():
                    status = "🟢 Running" if info['running'] else "🔴 Stopped"
                    click.echo(f"  {project}: {status}")
        
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            sys.exit(1)


    @cli.group()
    def hooks():
        """Git hooks management."""
        pass


    @hooks.command()
    @project_options
    @common_options
    @click.option('--indexer-path', help='Path to indexer executable')
    def install(project, collection, verbose, quiet, config, indexer_path):
        """Install git pre-commit hook."""
        
        try:
            project_path = Path(project).resolve()
            hooks_manager = GitHooksManager(str(project_path), collection)
            
            success = hooks_manager.install_pre_commit_hook(indexer_path, quiet=quiet)
            
            if not success:
                sys.exit(1)
        
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            sys.exit(1)


    @hooks.command()
    @project_options
    @common_options
    def uninstall(project, collection, verbose, quiet, config):
        """Uninstall git pre-commit hook."""
        
        try:
            project_path = Path(project).resolve()
            hooks_manager = GitHooksManager(str(project_path), collection)
            
            success = hooks_manager.uninstall_pre_commit_hook(quiet=quiet)
            
            if not success:
                sys.exit(1)
        
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            sys.exit(1)


    @hooks.command()
    @project_options
    @common_options
    def status(project, collection, verbose, quiet, config):
        """Show git hooks status."""
        
        try:
            project_path = Path(project).resolve()
            hooks_manager = GitHooksManager(str(project_path), collection)
            
            status_info = hooks_manager.get_hook_status()
            
            click.echo(f"Git repository: {'✅' if status_info['is_git_repo'] else '❌'}")
            click.echo(f"Hooks directory: {'✅' if status_info['hooks_dir_exists'] else '❌'}")
            click.echo(f"Pre-commit hook: {'✅ Installed' if status_info['hook_installed'] else '❌ Not installed'}")
            
            if status_info['hook_installed']:
                click.echo(f"Hook executable: {'✅' if status_info['hook_executable'] else '❌'}")
                if verbose and 'indexer_command' in status_info:
                    click.echo(f"Command: {status_info['indexer_command']}")
        
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            sys.exit(1)


    @cli.command()
    @project_options
    @click.argument('query')
    @click.option('--limit', type=int, default=10, help='Maximum results')
    @click.option('--type', 'result_type', type=click.Choice(['entity', 'relation', 'chat', 'all']), 
                  help='Filter by result type (default: all)')
    @common_options
    def search(project, collection, query, limit, result_type, verbose, quiet, config):
        """Search across code entities, relations, and chat conversations."""
        
        try:
            # Load configuration
            config_obj = load_config(Path(config) if config else None)
            
            # Create components
            embedder = create_embedder_from_config({
                "provider": "openai",
                "api_key": config_obj.openai_api_key,
                "model": "text-embedding-3-small"
            })
            
            vector_store = create_store_from_config({
                "backend": "qdrant",
                "url": config_obj.qdrant_url,
                "api_key": config_obj.qdrant_api_key
            })
            
            # Create indexer and search
            project_path = Path(project).resolve()
            indexer = CoreIndexer(config_obj, embedder, vector_store, project_path)
            
            # Handle unified search across different types
            if result_type == 'all' or result_type is None:
                # Search all types and combine results
                all_results = []
                
                # Search code entities and relations
                code_results = indexer.search_similar(collection, query, limit, None)
                all_results.extend(code_results)
                
                # Search chat conversations specifically
                chat_results = indexer.search_similar(collection, query, limit, 'chat_history')
                all_results.extend(chat_results)
                
                # Sort by score and limit to requested amount
                all_results.sort(key=lambda x: x.get('score', 0), reverse=True)
                results = all_results[:limit]
            elif result_type == 'chat':
                # Search only chat conversations
                results = indexer.search_similar(collection, query, limit, 'chat_history')
            else:
                # Search specific type (entity, relation)
                results = indexer.search_similar(collection, query, limit, result_type)
            
            if results:
                if not quiet:
                    click.echo(f"🔍 Found {len(results)} results for: {query}")
                    click.echo()
                
                for i, result in enumerate(results, 1):
                    score = result.get('score', 0)
                    payload = result.get('payload', {})
                    
                    click.echo(f"{i}. {payload.get('name', 'Unknown')} (score: {score:.3f})")
                    
                    if verbose:
                        entity_type = payload.get('entityType', payload.get('type', 'unknown'))
                        click.echo(f"   Type: {entity_type}")
                        
                        if 'file_path' in payload:
                            click.echo(f"   File: {payload['file_path']}")
                        
                        if 'observations' in payload:
                            obs = payload['observations'][:2]  # First 2 observations
                            for ob in obs:
                                click.echo(f"   📝 {ob}")
                        
                        click.echo()
            else:
                if not quiet:
                    click.echo(f"🔍 No results found for: {query}")
        
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            sys.exit(1)


    @cli.command('add-mcp')
    @click.option('--collection', '-c', required=True, help='Collection name for MCP server')
    @common_options
    def add_mcp(collection, verbose, quiet, config):
        """Add MCP server configuration for a collection."""
        
        try:
            # Validate collection name
            if not collection.replace('-', '').replace('_', '').isalnum():
                click.echo("❌ Collection name should only contain letters, numbers, hyphens, and underscores", err=True)
                sys.exit(1)
            
            if not quiet:
                click.echo(f"🔧 Setting up MCP server for collection: {collection}")
            
            # Load configuration
            config_obj = load_config(Path(config) if config else None)
            
            # Determine MCP server path (relative to current script location)
            script_dir = Path(__file__).parent.parent.absolute()  # Go up to project root
            mcp_server_path = script_dir / "mcp-qdrant-memory" / "dist" / "index.js"
            
            if not mcp_server_path.exists():
                click.echo(f"❌ MCP server not found at: {mcp_server_path}", err=True)
                click.echo("Run the installation steps first:", err=True)
                click.echo("git clone https://github.com/delorenj/mcp-qdrant-memory.git", err=True)
                click.echo("cd mcp-qdrant-memory && npm install && npm run build", err=True)
                sys.exit(1)
            
            server_name = f"{collection}-memory"
            
            # Build command to add MCP server
            cmd = [
                "claude", "mcp", "add", server_name,
                "-e", f"OPENAI_API_KEY={config_obj.openai_api_key}",
                "-e", f"QDRANT_API_KEY={config_obj.qdrant_api_key}",
                "-e", f"QDRANT_URL={config_obj.qdrant_url}",
                "-e", f"QDRANT_COLLECTION_NAME={collection}",
                "--",
                "node", str(mcp_server_path)
            ]
            
            # Add Voyage AI settings if configured
            if hasattr(config_obj, 'voyage_api_key') and config_obj.voyage_api_key:
                cmd.insert(-3, "-e")
                cmd.insert(-3, f"VOYAGE_API_KEY={config_obj.voyage_api_key}")
            
            if hasattr(config_obj, 'embedding_provider') and config_obj.embedding_provider:
                cmd.insert(-3, "-e")
                cmd.insert(-3, f"EMBEDDING_PROVIDER={config_obj.embedding_provider}")
                
            if hasattr(config_obj, 'voyage_model') and config_obj.voyage_model:
                cmd.insert(-3, "-e")
                cmd.insert(-3, f"EMBEDDING_MODEL={config_obj.voyage_model}")
            
            if verbose:
                click.echo(f"🚀 Adding MCP server: {server_name}")
                click.echo(f"📊 Collection name: {collection}")
                click.echo(f"🔗 Server path: {mcp_server_path}")
            
            # Execute the command
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                if not quiet:
                    click.echo("✅ MCP server added successfully!")
                    click.echo(f"🎯 Server name: {server_name}")
                    click.echo(f"📁 Collection: {collection}")
                    click.echo()
                    click.echo("Next steps:")
                    click.echo("1. Restart Claude Code")
                    click.echo(f"2. Index your project: claude-indexer --project /path/to/project --collection {collection}")
                    click.echo(f"3. Test search: mcp__{server_name.replace('-', '_')}__search_similar('your query')")
            else:
                click.echo("❌ Failed to add MCP server", err=True)
                if verbose:
                    click.echo(f"STDOUT: {result.stdout}", err=True)
                    click.echo(f"STDERR: {result.stderr}", err=True)
                sys.exit(1)
                
        except FileNotFoundError:
            click.echo("❌ 'claude' command not found", err=True)
            click.echo("Make sure Claude Code is installed and in your PATH", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)


    @cli.group()
    def chat():
        """Chat history indexing and summarization commands."""
        pass


    @chat.command()
    @project_options
    @common_options
    @click.option('--limit', '-l', type=int, default=None, help='Limit number of conversations to process')
    @click.option('--inactive-hours', type=float, default=1.0, help='Consider conversations inactive after N hours')
    def index(project, collection, verbose, quiet, config, limit, inactive_hours):
        """Index Claude Code chat history files for a project."""
        try:
            # Load configuration
            config_obj = load_config(Path(config) if config else None)
            
            # Create components
            embedder = create_embedder_from_config(config_obj)
            store = create_store_from_config(config_obj)
            
            # Import chat modules
            from .chat.parser import ChatParser
            from .chat.summarizer import ChatSummarizer
            
            if not quiet:
                click.echo("⚡ Starting chat history indexing...")
            
            # Initialize chat parser and summarizer
            parser = ChatParser()
            summarizer = ChatSummarizer(config_obj)
            
            # Parse conversations
            project_path = Path(project).resolve()
            conversations = parser.parse_all_chats(project_path, limit=limit)
            
            if not conversations:
                if not quiet:
                    click.echo("📭 No chat conversations found")
                return
            
            # Filter inactive conversations if requested
            if inactive_hours > 0:
                active_conversations = [
                    conv for conv in conversations 
                    if not conv.metadata.is_inactive(inactive_hours)
                ]
                if not quiet and len(active_conversations) != len(conversations):
                    click.echo(f"🔍 Filtered to {len(active_conversations)} active conversations (inactive threshold: {inactive_hours}h)")
                conversations = active_conversations
            
            if not conversations:
                if not quiet:
                    click.echo("📭 No active conversations found")
                return
            
            if not quiet:
                click.echo(f"📚 Processing {len(conversations)} conversations...")
            
            # Generate summaries
            summaries = summarizer.batch_summarize(conversations)
            
            # Store summaries as entities
            success_count = 0
            error_count = 0
            
            for conversation, summary in zip(conversations, summaries):
                try:
                    # Create chat chunk from summary for v2.4 pure architecture
                    from .analysis.entities import Entity, EntityType, ChatChunk
                    
                    # Generate embedding
                    chat_content = " | ".join(summary.to_observations())
                    embedding_result = embedder.embed_text(chat_content)
                    
                    if embedding_result.success:
                        # Create chat chunk
                        chat_chunk = ChatChunk(
                            id=f"chat::{conversation.summary_key}::summary",
                            chat_id=conversation.summary_key,
                            chunk_type="chat_summary",
                            content=chat_content,
                            timestamp=str(conversation.metadata.start_time) if hasattr(conversation.metadata, 'start_time') else None
                        )
                        
                        # Create vector point
                        point = store.create_chat_chunk_point(chat_chunk, embedding_result.embedding, collection)
                        
                        # Store in vector database
                        result = store.batch_upsert(collection, [point])
                        
                        if result.success:
                            success_count += 1
                            if verbose:
                                click.echo(f"  ✅ Indexed: {conversation.metadata.session_id}")
                        else:
                            error_count += 1
                            if verbose:
                                click.echo(f"  ❌ Failed to store: {conversation.metadata.session_id}")
                    else:
                        error_count += 1
                        if verbose:
                            click.echo(f"  ❌ Failed to embed: {conversation.metadata.session_id}")
                            
                except Exception as e:
                    error_count += 1
                    if verbose:
                        click.echo(f"  ❌ Error processing {conversation.metadata.session_id}: {e}")
            
            # Summary output
            if not quiet:
                if success_count > 0:
                    click.echo(f"✅ Successfully indexed {success_count} chat conversations")
                if error_count > 0:
                    click.echo(f"❌ Failed to index {error_count} conversations")
                    
        except Exception as e:
            click.echo(f"❌ Chat indexing failed: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)


    @chat.command()
    @project_options
    @common_options
    @click.option('--output-dir', type=click.Path(), help='Output directory for summary files')
    @click.option('--format', type=click.Choice(['json', 'markdown', 'text']), default='markdown', help='Output format')
    def summarize(project, collection, verbose, quiet, config, output_dir, format):
        """Generate summary files from indexed chat conversations."""
        try:
            # Load configuration
            config_obj = load_config(Path(config) if config else None)
            
            # Import chat modules
            from .chat.parser import ChatParser
            from .chat.summarizer import ChatSummarizer
            
            if not quiet:
                click.echo("📝 Generating chat conversation summaries...")
            
            # Initialize components
            parser = ChatParser()
            summarizer = ChatSummarizer(config_obj)
            
            # Parse conversations
            project_path = Path(project).resolve()
            conversations = parser.parse_all_chats(project_path)
            
            if not conversations:
                if not quiet:
                    click.echo("📭 No chat conversations found")
                return
            
            # Generate summaries
            summaries = summarizer.batch_summarize(conversations)
            
            # Prepare output directory
            if output_dir:
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)
            else:
                output_path = project_path / "chat_summaries"
                output_path.mkdir(exist_ok=True)
            
            # Write summary files
            success_count = 0
            for conversation, summary in zip(conversations, summaries):
                try:
                    session_id = conversation.metadata.session_id
                    
                    if format == 'json':
                        import json
                        filename = f"{session_id}_summary.json"
                        content = {
                            "session_id": session_id,
                            "project_path": conversation.metadata.project_path,
                            "summary": summary.summary,
                            "key_insights": summary.key_insights,
                            "topics": summary.topics,
                            "category": summary.category,
                            "code_patterns": summary.code_patterns,
                            "debugging_info": summary.debugging_info,
                            "message_count": conversation.metadata.message_count,
                            "duration_minutes": conversation.metadata.duration_minutes
                        }
                        with open(output_path / filename, 'w') as f:
                            json.dump(content, f, indent=2)
                    
                    elif format == 'markdown':
                        filename = f"{session_id}_summary.md"
                        content = f"""# Chat Summary: {session_id}

**Project:** {conversation.metadata.project_path}
**Duration:** {conversation.metadata.duration_minutes:.1f} minutes
**Messages:** {conversation.metadata.message_count}
**Category:** {summary.category or 'uncategorized'}

## Summary
{summary.summary}

## Key Insights
{chr(10).join(f"- {insight}" for insight in summary.key_insights)}

## Topics
{', '.join(summary.topics)}

## Code Patterns
{chr(10).join(f"- {pattern}" for pattern in summary.code_patterns)}

## Debugging Information
{chr(10).join(f"- **{k}:** {v}" for k, v in summary.debugging_info.items())}
"""
                        with open(output_path / filename, 'w') as f:
                            f.write(content)
                    
                    else:  # text format
                        filename = f"{session_id}_summary.txt"
                        content = f"""Chat Summary: {session_id}
Project: {conversation.metadata.project_path}
Duration: {conversation.metadata.duration_minutes:.1f} minutes
Messages: {conversation.metadata.message_count}
Category: {summary.category or 'uncategorized'}

Summary:
{summary.summary}

Key Insights:
{chr(10).join(f"- {insight}" for insight in summary.key_insights)}

Topics: {', '.join(summary.topics)}
Code Patterns: {', '.join(summary.code_patterns)}
"""
                        with open(output_path / filename, 'w') as f:
                            f.write(content)
                    
                    success_count += 1
                    if verbose:
                        click.echo(f"  ✅ Generated: {filename}")
                        
                except Exception as e:
                    if verbose:
                        click.echo(f"  ❌ Failed to generate summary for {conversation.metadata.session_id}: {e}")
            
            if not quiet:
                click.echo(f"✅ Generated {success_count} summary files in {output_path}")
                
        except Exception as e:
            click.echo(f"❌ Summary generation failed: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)


    @chat.command()
    @project_options
    @common_options
    @click.argument('query')
    @click.option('--limit', '-l', type=int, default=10, help='Maximum number of results')
    def search(project, collection, verbose, quiet, config, query, limit):
        """Search indexed chat conversations by content."""
        try:
            # Load configuration and create components
            config_obj = load_config(Path(config) if config else None)
            embedder = create_embedder_from_config(config_obj)
            store = create_store_from_config(config_obj)
            
            if not quiet:
                click.echo(f"🔍 Searching chat conversations for: {query}")
            
            # Generate query embedding
            embedding_result = embedder.embed_text(query)
            if not embedding_result.success:
                click.echo("❌ Failed to generate embedding for query", err=True)
                sys.exit(1)
            
            # Search vector store with chat_history filter
            search_result = store.search_similar(
                collection_name=collection,
                query_vector=embedding_result.embedding,
                limit=limit,
                filter_conditions={"type": "chat_history"}
            )
            
            if search_result.success and search_result.results:
                if not quiet:
                    click.echo(f"📚 Found {len(search_result.results)} relevant conversations:")
                    
                for i, result in enumerate(search_result.results, 1):
                    score = result.get('score', 0.0)
                    name = result.get('name', 'Unknown')
                    observations = result.get('observations', [])
                    
                    click.echo(f"\n{i}. **{name}** (similarity: {score:.3f})")
                    for obs in observations[:3]:  # Show first 3 observations
                        click.echo(f"   {obs}")
                    if len(observations) > 3:
                        click.echo(f"   ... and {len(observations) - 3} more")
            else:
                if not quiet:
                    click.echo(f"🔍 No chat conversations found for: {query}")
        
        except Exception as e:
            click.echo(f"❌ Search failed: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)


    @chat.command()
    @project_options  
    @common_options
    @click.option('--output', '-o', type=click.Path(), help='Output HTML file path (auto-generated if not specified)')
    @click.option('--conversation-id', help='Specific conversation ID to generate report for (uses most recent if not specified)')
    def html_report(project, collection, verbose, quiet, config, output, conversation_id):
        """Generate HTML report with GPT analysis and full conversation display."""
        try:
            # Load configuration
            config_obj = load_config(Path(config) if config else None)
            
            if not quiet:
                click.echo("📄 Generating HTML chat report...")
            
            # Import chat modules
            from .chat.html_report import ChatHtmlReporter
            from .chat.parser import ChatParser
            
            project_path = Path(project).resolve()
            if not project_path.exists():
                click.echo(f"❌ Project directory not found: {project_path}", err=True)
                sys.exit(1)
            
            # Initialize reporter and parser
            reporter = ChatHtmlReporter(config_obj)
            parser = ChatParser()
            
            # Determine conversation input
            if conversation_id:
                # Try to find specific conversation file
                chat_files = parser.get_chat_files(project_path)
                conversation_file = None
                for file_path in chat_files:
                    if conversation_id in file_path.stem:
                        conversation_file = file_path
                        break
                
                if not conversation_file:
                    click.echo(f"❌ Conversation ID '{conversation_id}' not found", err=True)
                    sys.exit(1)
                    
                conversation_input = conversation_file
            else:
                # Use most recent conversation
                chat_files = parser.get_chat_files(project_path)
                if not chat_files:
                    click.echo(f"❌ No chat conversations found for project: {project_path}", err=True)
                    sys.exit(1)
                
                conversation_input = chat_files[0]  # Most recent
            
            if verbose:
                click.echo(f"📝 Processing conversation: {conversation_input}")
            
            # Generate output path if not specified
            output_path = None
            if output:
                output_path = Path(output)
            
            # Generate HTML report
            html_file = reporter.generate_report(conversation_input, output_path)
            
            if not quiet:
                click.echo(f"✅ HTML report generated: {html_file}")
                click.echo(f"🌐 Open in browser: file://{html_file.absolute()}")
        
        except Exception as e:
            click.echo(f"❌ HTML report generation failed: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)

    # End of Click-available conditional block

if __name__ == '__main__':
    cli()