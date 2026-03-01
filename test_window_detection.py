from actions.os_control import open_app
import pygetwindow as gw
import time

print("Testing Windows Open Detection")
print("==============================")
print("Currently Open Windows:")
for w in gw.getAllWindows():
    if w.title and w.visible:
        print(f" - {w.title}")

print("\nAttempting to 'open' notepad...")
open_app("notepad")
print("Done. Did it fallback to search, or did it bring it to front?")
time.sleep(1)

print("\nAttempting to 'open' chrome...")
open_app("chrome")
print("Done. Did it fallback to search?")
