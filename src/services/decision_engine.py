"""Decision Engine for intelligent response routing and prioritization."""
import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from enum import Enum
import re

logger = logging.getLogger(__name__)


class MessagePriority(Enum):
    """Message priority levels."""
    URGENT = 1  # Mod messages, alerts
    HIGH = 2    # Subscribers, VIPs
    NORMAL = 3  # Regular viewers
    LOW = 4     # Potential spam, repetitive


class ResponseAction(Enum):
    """Actions the decision engine can take."""
    RESPOND_TTS = "respond_with_tts"  # Full response with TTS
    RESPOND_TEXT = "respond_text_only"  # Text response only
    ACKNOWLEDGE = "acknowledge"  # Simple acknowledgment
    IGNORE = "ignore"  # Don't respond
    MODERATE = "moderate"  # Flag for moderation


class DecisionEngine:
    """Make intelligent decisions about message handling.
    
    Determines priority, routing, and response strategy for messages.
    """
    
    def __init__(
        self,
        response_generator=None,
        memory_system=None,
        personality_engine=None
    ):
        """Initialize decision engine."""
        self.response_gen = response_generator
        self.memory = memory_system
        self.personality = personality_engine
        
        # Decision tracking
        self.recent_decisions: List[Dict] = []
        self.user_interaction_counts: Dict[str, int] = {}
        self.spam_patterns: List[re.Pattern] = self._compile_spam_patterns()
        
        # Rate limiting
        self.user_last_response: Dict[str, datetime] = {}
        self.response_cooldown = 5  # seconds between responses to same user
        
        logger.info("DecisionEngine initialized")
        
    async def decide(
        self,
        message: str,
        user: str,
        user_roles: List[str] = None,
        context: Dict[str, Any] = None
    ) -> Tuple[ResponseAction, MessagePriority, Dict[str, Any]]:
        """Decide how to handle a message.
        
        Args:
            message: The message content
            user: Username
            user_roles: User's roles (mod, sub, vip, etc.)
            context: Additional context
            
        Returns:
            Tuple of (action, priority, metadata)
        """
        # Determine priority
        priority = self._calculate_priority(user, user_roles, message)
        
        # Check for spam
        if self._is_spam(message):
            logger.debug(f"Message from {user} flagged as spam")
            return ResponseAction.IGNORE, MessagePriority.LOW, {'reason': 'spam'}
            
        # Check rate limiting
        if not self._check_rate_limit(user, priority):
            logger.debug(f"Rate limit hit for {user}")
            return ResponseAction.IGNORE, priority, {'reason': 'rate_limit'}
            
        # Determine action based on context
        action = await self._determine_action(
            message, user, priority, context
        )
        
        # Build metadata
        metadata = {
            'timestamp': datetime.now().isoformat(),
            'interaction_count': self.user_interaction_counts.get(user, 0),
            'confidence': self._calculate_confidence(message, context)
        }
        
        # Track decision
        self._track_decision(user, action, priority, metadata)
        
        return action, priority, metadata
        
    def _calculate_priority(
        self,
        user: str,
        user_roles: Optional[List[str]],
        message: str
    ) -> MessagePriority:
        """Calculate message priority based on user and content."""
        # Check user roles
        if user_roles:
            if 'moderator' in user_roles or 'broadcaster' in user_roles:
                return MessagePriority.URGENT
            elif 'subscriber' in user_roles or 'vip' in user_roles:
                return MessagePriority.HIGH
                
        # Check message content
        message_lower = message.lower()
        
        # Urgent keywords
        urgent_keywords = ['!alert', '!mod', 'emergency', 'urgent']
        if any(keyword in message_lower for keyword in urgent_keywords):
            return MessagePriority.URGENT
            
        # Check interaction history
        interaction_count = self.user_interaction_counts.get(user, 0)
        if interaction_count > 10:
            return MessagePriority.HIGH
            
        return MessagePriority.NORMAL
        
    async def _determine_action(
        self,
        message: str,
        user: str,
        priority: MessagePriority,
        context: Optional[Dict]
    ) -> ResponseAction:
        """Determine the appropriate action for a message."""
        message_lower = message.lower()
        
        # Check for moderation triggers
        if self._needs_moderation(message):
            return ResponseAction.MODERATE
            
        # High priority always gets TTS
        if priority in [MessagePriority.URGENT, MessagePriority.HIGH]:
            return ResponseAction.RESPOND_TTS
            
        # Check message type
        if len(message) < 10:
            # Short messages get acknowledgment
            return ResponseAction.ACKNOWLEDGE
            
        # Questions get full responses
        if '?' in message:
            return ResponseAction.RESPOND_TTS
            
        # Check if user is actively engaging
        if self.user_interaction_counts.get(user, 0) > 3:
            return ResponseAction.RESPOND_TEXT
            
        # Default action based on context
        if context and context.get('stream_active'):
            # During active stream, be selective
            return ResponseAction.ACKNOWLEDGE
        else:
            # Off-stream, more responsive
            return ResponseAction.RESPOND_TEXT
            
    def _is_spam(self, message: str) -> bool:
        """Check if message matches spam patterns."""
        message_lower = message.lower()
        
        # Check against compiled patterns
        for pattern in self.spam_patterns:
            if pattern.search(message_lower):
                return True
                
        # Check for excessive repetition
        words = message_lower.split()
        if len(words) > 3:
            unique_words = set(words)
            if len(unique_words) < len(words) / 3:
                return True  # Too much repetition
                
        # Check for excessive caps
        if len(message) > 10:
            caps_ratio = sum(1 for c in message if c.isupper()) / len(message)
            if caps_ratio > 0.8:
                return True
                
        return False
        
    def _needs_moderation(self, message: str) -> bool:
        """Check if message needs moderation."""
        # This would integrate with actual moderation rules
        # For now, simple keyword check
        bad_words = ['spam', 'scam', 'hack']  # Would be more comprehensive
        message_lower = message.lower()
        
        return any(word in message_lower for word in bad_words)
        
    def _check_rate_limit(self, user: str, priority: MessagePriority) -> bool:
        """Check if user is within rate limits."""
        # Higher priority bypasses rate limiting
        if priority in [MessagePriority.URGENT, MessagePriority.HIGH]:
            return True
            
        # Check last response time
        if user in self.user_last_response:
            time_since_last = datetime.now() - self.user_last_response[user]
            if time_since_last < timedelta(seconds=self.response_cooldown):
                return False
                
        return True
        
    def _calculate_confidence(self, message: str, context: Optional[Dict]) -> float:
        """Calculate confidence score for response decision."""
        confidence = 0.5  # Base confidence
        
        # Adjust based on message clarity
        if len(message) > 20 and '?' in message:
            confidence += 0.2  # Clear question
            
        # Adjust based on context availability
        if context:
            if 'user_history' in context:
                confidence += 0.1
            if 'stream_topic' in context:
                confidence += 0.1
                
        # Cap at 1.0
        return min(confidence, 1.0)
        
    def _track_decision(
        self,
        user: str,
        action: ResponseAction,
        priority: MessagePriority,
        metadata: Dict
    ):
        """Track decision for learning and analytics."""
        # Update interaction count
        self.user_interaction_counts[user] = \
            self.user_interaction_counts.get(user, 0) + 1
            
        # Update last response time if responding
        if action in [ResponseAction.RESPOND_TTS, ResponseAction.RESPOND_TEXT]:
            self.user_last_response[user] = datetime.now()
            
        # Store decision
        decision = {
            'user': user,
            'action': action.value,
            'priority': priority.value,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata
        }
        
        self.recent_decisions.append(decision)
        
        # Keep only last 100 decisions
        if len(self.recent_decisions) > 100:
            self.recent_decisions = self.recent_decisions[-100:]
            
    def _compile_spam_patterns(self) -> List[re.Pattern]:
        """Compile regex patterns for spam detection."""
        patterns = [
            r'(.)\\1{5,}',  # Same character repeated 6+ times
            r'(\\w+\\s*)\\1{3,}',  # Same word repeated 4+ times
            r'bit\\.ly|tinyurl|goo\\.gl',  # URL shorteners
            r'follow.*@\\w+',  # Follow spam
            r'(free|win|prize|giveaway).*click',  # Scam patterns
        ]
        
        return [re.compile(p, re.IGNORECASE) for p in patterns]
        
    async def learn_from_feedback(
        self,
        user: str,
        action: ResponseAction,
        was_correct: bool
    ):
        """Learn from feedback about decisions.
        
        Args:
            user: Username
            action: The action that was taken
            was_correct: Whether the decision was correct
        """
        # This would implement learning logic
        # For now, just log it
        logger.info(f"Feedback: {action.value} for {user} was {'correct' if was_correct else 'incorrect'}")
        
        # Adjust future behavior based on feedback
        if not was_correct:
            if action == ResponseAction.IGNORE:
                # Maybe we should have responded
                self.response_cooldown = max(3, self.response_cooldown - 1)
            elif action in [ResponseAction.RESPOND_TTS, ResponseAction.RESPOND_TEXT]:
                # Maybe we're responding too much
                self.response_cooldown = min(10, self.response_cooldown + 1)
                
    def get_stats(self) -> Dict[str, Any]:
        """Get decision engine statistics."""
        if not self.recent_decisions:
            return {
                'total_decisions': 0,
                'actions': {},
                'priorities': {},
                'active_users': 0
            }
            
        # Count actions
        action_counts = {}
        priority_counts = {}
        
        for decision in self.recent_decisions:
            action = decision['action']
            priority = decision['priority']
            
            action_counts[action] = action_counts.get(action, 0) + 1
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
            
        return {
            'total_decisions': len(self.recent_decisions),
            'actions': action_counts,
            'priorities': priority_counts,
            'active_users': len(self.user_interaction_counts),
            'response_cooldown': self.response_cooldown
        }