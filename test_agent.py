from agent import run_agent

def console_logger(msg):
    print(msg)

if __name__ == "__main__":
    print("==================================================")
    print("Testing Agent Logic without Audio...")
    print("==================================================")
    
    # Fake transcript that bypasses the broken Audio module
    fake_transcript = "Open Google Docs in the browser and create a new blank document."
    
    print(f"Submitting Fake Transcript: '{fake_transcript}'\n")
    
    try:
        # Pass it straight into the refactored reasoning loop!
        final_message = run_agent(fake_transcript, update_log_callback=console_logger)
        print("\nAgent finished! Final Message:", final_message)
        
    except Exception as e:
        print("\nAgent crashed during testing:", str(e))
    
    print("\nTest complete.")