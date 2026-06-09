import asyncio
import sys

from app.agent.graph import run_agent
from app.agent import memory as mem
from app.core.logger import get_logger

logger = get_logger("local_chat")

async def main():
    print("==================================================")
    print("🤖 Local CLI Chat for Human-Sounding WhatsApp Agent")
    print("Type 'quit' or 'exit' to stop.")
    print("==================================================")
    
    # We will use a mock phone number for local testing
    phone = "+1234567890" 
    
    # Initialize DB (creates tables if they don't exist)
    await mem.init_db()
    
    print(f"\nStarted session with mock number: {phone}\n")
    
    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() in ['quit', 'exit']:
                break
            
            if not user_input.strip():
                continue
                
            # Persist user's message
            await mem.add_message(phone, "user", user_input)
            
            # Run the agent
            response = await run_agent(phone, user_input)
            
            # Persist the agent's response
            await mem.add_message(phone, "assistant", response)
            
            # Update message count
            profile = await mem.get_or_create_profile(phone)
            new_count = profile.get("message_count", 0) + 2
            await mem.update_profile(phone, {"message_count": new_count})
            
            print(f"Agent: {response}\n")
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    # Workaround for Windows asyncio event loop policies if needed
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
