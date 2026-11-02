import psutil
import time

def get_system_info():
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage('/').percent
    current_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    return cpu_usage, memory_usage, disk_usage, current_time

def main():
    while True:
        cpu_usage, memory_usage, disk_usage, current_time = get_system_info()
        print(f"CPU Usage: {cpu_usage}% | Memory Usage: {memory_usage}% | Disk Usage: {disk_usage}% | Current Time: {current_time}")
        time.sleep(2)

if __name__ == "__main__":
    main()