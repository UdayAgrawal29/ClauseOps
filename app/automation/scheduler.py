from apscheduler.schedulers.blocking import BlockingScheduler
from app.automation.task_engine import generate_tasks
from app.db import ensure_tables

def start_scheduler():
    # Ensure DB is ready
    ensure_tables()
    
    scheduler = BlockingScheduler()
    
    # Schedule the task engine to run every 10 seconds
    scheduler.add_job(generate_tasks, 'interval', seconds=10)
    
    print("------------------------------------------------")
    print("🚀 LexFlow Automation Engine is Running...")
    print("   Checking for new contract analysis every 10s.")
    print("   Press Ctrl+C to stop.")
    print("------------------------------------------------")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    start_scheduler()