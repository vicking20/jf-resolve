import sys
sys.path.insert(0, 'libs')
import os
import time
import argparse
import signal
from datetime import datetime, timedelta
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from config_loader import load_config
from media_requester import run_jellyseerr_discovery
from sorter import scan_and_resolve
from updater import main as updater_main

class MediaController:
    def __init__(self, use_monitoring=True):
        self.cfg = load_config()
        self.use_monitoring = use_monitoring
        self.running = True
        self.last_scan_time = datetime.now()
        
        # Get paths from config
        self.radarr_blackhole = self.cfg.get("RADARR_BLACKHOLE", "./radarr/blackhole")
        self.sonarr_blackhole = self.cfg.get("SONARR_BLACKHOLE", "./sonarr/blackhole")
        
        # Scheduling intervals
        self.jellyseerr_interval = 12 * 60 * 60  # 24 hours now 12 hours
        self.updater_interval = 12 * 60 * 60     # 24 hours before, now 12 hours
        self.fallback_scan_interval = 15 * 60    # 15 minutes
        
        # Track last execution times
        self.last_jellyseerr_run = datetime.now() - timedelta(hours=25)  # Force initial run
        self.last_updater_run = datetime.now() - timedelta(hours=25)     # Force initial run
        self.last_fallback_scan = datetime.now()
        
        # File monitoring
        self.observer = None
        self.pending_scan = False
        self.file_detected_time = None
        
        print("Media Controller initialized")
        print(f"Monitoring: {self.use_monitoring}")
        print(f"Radarr blackhole: {self.radarr_blackhole}")
        print(f"Sonarr blackhole: {self.sonarr_blackhole}")

    def log(self, message):
        """Log with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}")

    def run_jellyseerr_discovery(self):
        """Run Jellyseerr discovery"""
        try:
            self.log("Starting Jellyseerr discovery...")
            run_jellyseerr_discovery()
            self.last_jellyseerr_run = datetime.now()
            self.log("Jellyseerr discovery completed")
        except Exception as e:
            self.log(f"Error in Jellyseerr discovery: {e}")

    def run_scan_and_resolve(self, scan_type="regular"):
        """Run scan and resolve"""
        try:
            self.log(f"Starting scan and resolve ({scan_type})...")
            scan_and_resolve()
            self.last_scan_time = datetime.now()
            self.log(f"Scan and resolve completed ({scan_type})")
        except Exception as e:
            self.log(f"Error in scan and resolve ({scan_type}): {e}")

    def run_updater(self):
        """Run link updater"""
        try:
            self.log("Starting link updater...")
            updater_main()
            self.last_updater_run = datetime.now()
            self.log("Link updater completed")
        except Exception as e:
            self.log(f"Error in link updater: {e}")

    def check_scheduled_tasks(self):
        """Check and run scheduled tasks"""
        now = datetime.now()
        
        # Check for Jellyseerr discovery (every 24 hours)
        if (now - self.last_jellyseerr_run).total_seconds() >= self.jellyseerr_interval:
            self.run_jellyseerr_discovery()
        
        # Check for updater (every 24 hours)
        if (now - self.last_updater_run).total_seconds() >= self.updater_interval:
            self.run_updater()

    def check_file_triggered_scan(self):
        """Check if we need to run a scan due to file detection"""
        if not self.pending_scan or not self.file_detected_time:
            return
            
        now = datetime.now()
        # Wait 30 seconds after file detection before scanning
        if (now - self.file_detected_time).total_seconds() >= 30:
            self.log("Running file-triggered scan...")
            self.run_scan_and_resolve("file-triggered")
            self.pending_scan = False
            self.file_detected_time = None

    def check_fallback_scan(self):
        """Check if we need to run a fallback scan"""
        now = datetime.now()
        
        # Only run fallback if no monitoring or enough time has passed
        if not self.use_monitoring or (now - self.last_fallback_scan).total_seconds() >= self.fallback_scan_interval:
            # Only run if no recent scans from any source
            time_since_last = (now - self.last_scan_time).total_seconds()
            if time_since_last >= self.fallback_scan_interval:
                self.log("Running fallback scan...")
                self.run_scan_and_resolve("fallback")
            self.last_fallback_scan = now

    def on_file_detected(self, file_path):
        """Handle file detection from monitoring"""
        self.log(f"New file detected: {file_path}")
        self.pending_scan = True
        self.file_detected_time = datetime.now()

    def main_loop(self):
        """Main execution loop"""
        self.log("Starting main loop...")
        
        while self.running:
            try:
                # Check scheduled tasks (jellyseerr and updater)
                self.check_scheduled_tasks()
                
                # Check for file-triggered scans
                self.check_file_triggered_scan()
                
                # Check for fallback scans
                self.check_fallback_scan()
                
                # Sleep for 10 seconds before next check
                time.sleep(10)
                
            except Exception as e:
                self.log(f"Error in main loop: {e}")
                time.sleep(30)  # Wait longer if there's an error

class BlackholeHandler(FileSystemEventHandler):
    """File system event handler for blackhole folders"""
    def __init__(self, controller):
        self.controller = controller

    def on_created(self, event):
        if not event.is_directory:
            if event.src_path.endswith(('.torrent', '.magnet')):
                self.controller.on_file_detected(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            if event.dest_path.endswith(('.torrent', '.magnet')):
                self.controller.on_file_detected(event.dest_path)

def setup_monitoring(controller):
    """Setup file system monitoring"""
    if not controller.use_monitoring:
        return None
        
    try:
        observer = Observer()
        handler = BlackholeHandler(controller)
        
        paths_to_monitor = [controller.radarr_blackhole, controller.sonarr_blackhole]
        for path in paths_to_monitor:
            if os.path.exists(path):
                observer.schedule(handler, path, recursive=False)
                controller.log(f"Monitoring: {path}")
            else:
                controller.log(f"Warning: Path does not exist: {path}")
        
        observer.start()
        return observer
    except Exception as e:
        controller.log(f"Failed to setup monitoring: {e}")
        controller.log("Falling back to periodic scanning")
        controller.use_monitoring = False
        return None

def signal_handler(signum, frame, controller):
    """Handle shutdown signals"""
    controller.log("Received shutdown signal, stopping...")
    controller.running = False

def main():
    parser = argparse.ArgumentParser(description='Media Controller')
    parser.add_argument('--initiate', action='store_true', 
                       help='Run initial Jellyseerr discovery and scan on startup')
    parser.add_argument('--no-monitoring', action='store_true',
                       help='Disable file system monitoring, use periodic scanning only')
    parser.add_argument('--scan-interval', type=int, default=15,
                       help='Fallback scan interval in minutes (default: 15)')
    
    args = parser.parse_args()
    use_monitoring = not args.no_monitoring
    
    controller = MediaController(use_monitoring=use_monitoring)
    
    if args.scan_interval:
        controller.fallback_scan_interval = args.scan_interval * 60
        controller.log(f"Fallback scan interval set to {args.scan_interval} minutes")
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, lambda s, f: signal_handler(s, f, controller))
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s, f, controller))
    
    try:
        # Run initial tasks if requested
        if args.initiate:
            controller.log("Initial run requested...")
            controller.run_jellyseerr_discovery()
            time.sleep(5)
            controller.run_scan_and_resolve("initial")
        
        # Setup file monitoring
        controller.observer = setup_monitoring(controller)
        
        controller.log("Media Controller started successfully")
        controller.log("Press Ctrl+C to stop")
        
        # Start main loop
        controller.main_loop()
        
    except KeyboardInterrupt:
        controller.log("Keyboard interrupt received")
    except Exception as e:
        controller.log(f"Fatal error: {e}")
    finally:
        controller.log("Shutting down...")
        controller.running = False
        
        # Cleanup
        if controller.observer:
            controller.observer.stop()
            controller.observer.join()
        
        controller.log("Media Controller stopped")

if __name__ == "__main__":
    main()