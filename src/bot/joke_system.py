"""
Two-Part Joke Delivery System
Delivers jokes in two parts: setup then waits for user response before punchline
"""

import asyncio
import logging
import random
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
import json
import os

logger = logging.getLogger(__name__)


class JokeState(Enum):
    """State of joke delivery"""
    IDLE = "idle"
    SETUP_DELIVERED = "setup_delivered"
    PUNCHLINE_DELIVERED = "punchline_delivered"


class TwoPartJokeSystem:
    """
    Manages two-part joke delivery with user interaction
    Delivers setup, waits for any user input, then delivers punchline
    """
    
    def __init__(self, joke_file: str = "jokes_library.json"):
        """
        Initialize joke system
        
        Args:
            joke_file: Path to jokes library JSON file
        """
        self.joke_file = joke_file
        self.jokes = self._load_jokes()
        
        # Current joke state
        self.current_state = JokeState.IDLE
        self.current_joke: Optional[Dict[str, str]] = None
        self.setup_time: Optional[datetime] = None
        
        # Track users who can trigger punchline
        self.waiting_for_users: List[str] = []  # Empty = anyone can trigger
        
        # Cooldown management
        self.last_joke_time: Optional[datetime] = None
        self.joke_cooldown_seconds = 60  # Minimum time between jokes
        
        # Performance tracking
        self.jokes_started = 0
        self.jokes_completed = 0
        self.jokes_abandoned = 0
        
        # Recent jokes to avoid repetition
        self.recent_joke_ids: List[int] = []
        self.max_recent = 10
    
    def _load_jokes(self) -> List[Dict[str, str]]:
        """Load jokes from file or create defaults"""
        if os.path.exists(self.joke_file):
            try:
                with open(self.joke_file, 'r') as f:
                    data = json.load(f)
                    return data.get('jokes', self._get_default_jokes())
            except Exception as e:
                logger.error(f"Failed to load jokes: {e}")
        
        return self._get_default_jokes()
    
    def _get_default_jokes(self) -> List[Dict[str, str]]:
        """Get default joke library"""
        return [
            {
                "id": 1,
                "setup": "why don't scientists trust atoms",
                "punchline": "because they make up everything",
                "category": "science"
            },
            {
                "id": 2,
                "setup": "what do you call a bear with no teeth",
                "punchline": "a gummy bear",
                "category": "animals"
            },
            {
                "id": 3,
                "setup": "why did the scarecrow win an award",
                "punchline": "he was outstanding in his field",
                "category": "puns"
            },
            {
                "id": 4,
                "setup": "what's the best thing about switzerland",
                "punchline": "i don't know but the flag is a big plus",
                "category": "geography"
            },
            {
                "id": 5,
                "setup": "why don't eggs tell jokes",
                "punchline": "they'd crack up",
                "category": "food"
            },
            {
                "id": 6,
                "setup": "what do you call a fake noodle",
                "punchline": "an impasta",
                "category": "food"
            },
            {
                "id": 7,
                "setup": "why can't a bicycle stand up by itself",
                "punchline": "it's two tired",
                "category": "puns"
            },
            {
                "id": 8,
                "setup": "what do you call cheese that isn't yours",
                "punchline": "nacho cheese",
                "category": "food"
            },
            {
                "id": 9,
                "setup": "how do you organize a space party",
                "punchline": "you planet",
                "category": "space"
            },
            {
                "id": 10,
                "setup": "why did the math book look so sad",
                "punchline": "because it had too many problems",
                "category": "school"
            },
            {
                "id": 11,
                "setup": "what do you call a dinosaur that crashes his car",
                "punchline": "tyrannosaurus wrecks",
                "category": "dinosaurs"
            },
            {
                "id": 12,
                "setup": "why did the cookie go to the doctor",
                "punchline": "because it felt crumbly",
                "category": "food"
            },
            {
                "id": 13,
                "setup": "what did the ocean say to the beach",
                "punchline": "nothing it just waved",
                "category": "nature"
            },
            {
                "id": 14,
                "setup": "why do programmers prefer dark mode",
                "punchline": "because light attracts bugs",
                "category": "tech"
            },
            {
                "id": 15,
                "setup": "how many programmers does it take to change a light bulb",
                "punchline": "none that's a hardware problem",
                "category": "tech"
            }
        ]
    
    def can_start_joke(self) -> bool:
        """Check if we can start a new joke"""
        # Check if already in progress
        if self.current_state != JokeState.IDLE:
            return False
        
        # Check cooldown
        if self.last_joke_time:
            time_since_last = (datetime.now() - self.last_joke_time).total_seconds()
            if time_since_last < self.joke_cooldown_seconds:
                return False
        
        return True
    
    def start_joke(self, target_users: Optional[List[str]] = None) -> Optional[str]:
        """
        Start a new joke by delivering the setup
        
        Args:
            target_users: Optional list of users who can trigger punchline
                         If None, anyone can trigger it
        
        Returns:
            The joke setup text, or None if can't start
        """
        if not self.can_start_joke():
            return None
        
        # Select a joke that hasn't been used recently
        available_jokes = [j for j in self.jokes if j['id'] not in self.recent_joke_ids]
        if not available_jokes:
            # All jokes used recently, reset
            available_jokes = self.jokes
            self.recent_joke_ids = []
        
        # Select random joke
        self.current_joke = random.choice(available_jokes)
        
        # Track usage
        self.recent_joke_ids.append(self.current_joke['id'])
        if len(self.recent_joke_ids) > self.max_recent:
            self.recent_joke_ids.pop(0)
        
        # Set state
        self.current_state = JokeState.SETUP_DELIVERED
        self.setup_time = datetime.now()
        self.waiting_for_users = target_users or []
        self.jokes_started += 1
        
        logger.info(f"Starting joke #{self.current_joke['id']}: {self.current_joke['setup']}")
        
        # Return the setup with a prompt
        setup = self.current_joke['setup']
        if random.random() < 0.5:
            # Sometimes add a prompt
            setup = f"hey chat, {setup}?"
        else:
            setup = f"{setup}?"
        
        return setup
    
    def check_for_punchline_trigger(self, username: str, message: str) -> Optional[str]:
        """
        Check if a user message should trigger the punchline
        
        Args:
            username: User who sent the message
            message: The message content
        
        Returns:
            The punchline if triggered, None otherwise
        """
        # Not waiting for punchline
        if self.current_state != JokeState.SETUP_DELIVERED:
            return None
        
        # Check if user is allowed to trigger (if we have a target list)
        if self.waiting_for_users and username not in self.waiting_for_users:
            # Check if message seems like it's asking for the punchline anyway
            trigger_words = ['what', 'why', 'how', 'idk', 'dunno', '?', 'no idea', 'tell', 'know']
            if not any(word in message.lower() for word in trigger_words):
                return None
        
        # Any response triggers the punchline
        return self.deliver_punchline()
    
    def deliver_punchline(self) -> Optional[str]:
        """
        Deliver the punchline of the current joke
        
        Returns:
            The punchline text, or None if no joke in progress
        """
        if self.current_state != JokeState.SETUP_DELIVERED or not self.current_joke:
            return None
        
        # Update state
        self.current_state = JokeState.PUNCHLINE_DELIVERED
        self.jokes_completed += 1
        self.last_joke_time = datetime.now()
        
        punchline = self.current_joke['punchline']
        
        logger.info(f"Delivering punchline: {punchline}")
        
        # Reset after short delay
        asyncio.create_task(self._reset_after_delay())
        
        return punchline
    
    async def _reset_after_delay(self):
        """Reset joke state after punchline delivery"""
        await asyncio.sleep(5)  # Wait 5 seconds before allowing new joke
        self.current_state = JokeState.IDLE
        self.current_joke = None
        self.waiting_for_users = []
    
    def abandon_joke(self) -> None:
        """Abandon current joke if it times out"""
        if self.current_state == JokeState.SETUP_DELIVERED:
            self.jokes_abandoned += 1
            logger.info(f"Abandoning joke after timeout")
            
        self.current_state = JokeState.IDLE
        self.current_joke = None
        self.waiting_for_users = []
        self.last_joke_time = datetime.now()  # Still apply cooldown
    
    def check_timeout(self) -> bool:
        """
        Check if current joke has timed out
        
        Returns:
            True if joke should be abandoned
        """
        if self.current_state != JokeState.SETUP_DELIVERED:
            return False
        
        if not self.setup_time:
            return False
        
        # Timeout after 30 seconds
        time_waiting = (datetime.now() - self.setup_time).total_seconds()
        return time_waiting > 30
    
    def get_hint(self) -> Optional[str]:
        """
        Get a hint for the current joke (if waiting)
        
        Returns:
            Hint text or None
        """
        if self.current_state != JokeState.SETUP_DELIVERED:
            return None
        
        hints = [
            "anyone know the answer",
            "chat any ideas",
            "someone must know this one",
            "i'll wait",
            "take your time chat"
        ]
        
        return random.choice(hints)
    
    def get_stats(self) -> Dict[str, any]:
        """Get joke system statistics"""
        completion_rate = (
            self.jokes_completed / self.jokes_started * 100
            if self.jokes_started > 0 else 0
        )
        
        return {
            "state": self.current_state.value,
            "jokes_started": self.jokes_started,
            "jokes_completed": self.jokes_completed,
            "jokes_abandoned": self.jokes_abandoned,
            "completion_rate": round(completion_rate, 1),
            "total_jokes": len(self.jokes),
            "current_joke_id": self.current_joke['id'] if self.current_joke else None
        }
    
    def add_joke(self, setup: str, punchline: str, category: str = "general") -> bool:
        """
        Add a new joke to the library
        
        Args:
            setup: The joke setup
            punchline: The joke punchline
            category: Joke category
        
        Returns:
            True if added successfully
        """
        try:
            # Generate new ID
            max_id = max([j['id'] for j in self.jokes]) if self.jokes else 0
            new_id = max_id + 1
            
            # Add joke
            new_joke = {
                "id": new_id,
                "setup": setup.lower(),  # Keep lowercase for chat style
                "punchline": punchline.lower(),
                "category": category
            }
            
            self.jokes.append(new_joke)
            
            # Save to file
            self._save_jokes()
            
            logger.info(f"Added new joke #{new_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add joke: {e}")
            return False
    
    def _save_jokes(self) -> None:
        """Save jokes to file"""
        try:
            with open(self.joke_file, 'w') as f:
                json.dump({"jokes": self.jokes}, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save jokes: {e}")