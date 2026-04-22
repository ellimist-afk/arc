"""Response Generator with fallback chains for reliable chat responses."""
import asyncio
import json
import logging
import time
from typing import Dict, Optional, Any, List
from datetime import datetime, timedelta
import hashlib

logger = logging.getLogger(__name__)


class ResponseGenerator:
    """Generate responses with fallback chains.
    
    Integrates with Thread 2's message queue for chat processing.
    """
    
    # Response templates for final fallback
    FALLBACK_TEMPLATES = {
        'greeting': [
            "Hey there! Welcome to the stream!",
            "Hello! Great to see you here!",
            "Hi! Thanks for joining us!"
        ],
        'question': [
            "That's an interesting question!",
            "Let me think about that...",
            "Good question!"
        ],
        'default': [
            "Thanks for the message!",
            "I appreciate your input!",
            "Interesting point!"
        ]
    }
    
    def __init__(
        self,
        http_client=None,
        personality_engine=None,
        memory_system=None,
        tts_service=None
    ):
        """Initialize response generator.
        
        Args:
            http_client: ResilientHTTPClient from Thread 1
            personality_engine: PersonalityEngine instance
            memory_system: UnifiedMemory instance
            tts_service: TTSService for audio output
        """
        self.http = http_client
        self.personality = personality_engine
        self.memory = memory_system
        self.tts = tts_service
        
        # Response cache for fallback
        self.response_cache: Dict[str, str] = {}
        self.cache_ttl = 300  # 5 minutes
        self.cache_timestamps: Dict[str, datetime] = {}
        
        # Performance metrics
        self.response_times: List[float] = []
        self.fallback_count = 0
        self.success_count = 0
        
        logger.info("ResponseGenerator initialized")
        
    async def generate(
        self,
        message: str,
        user: str,
        streamer_id: str,
        priority: str = 'normal'
    ) -> Dict[str, Any]:
        """Generate response with fallbacks.
        
        Args:
            message: User's message
            user: Username
            streamer_id: Streamer identifier
            priority: Message priority (low, normal, high)
            
        Returns:
            Response dict with text and TTS data
        """
        start_time = time.perf_counter()
        
        try:
            # Build context from memory
            context = await self._build_context(user)
            
            # Load personality profile
            if self.personality:
                from src.personality.engine import PersonalityTraits
                personality = await self.personality.load_profile(streamer_id)
            else:
                personality = None
                
            # Try primary response generation
            response_text = await self._primary_response(
                message, context, personality
            )
            
            self.success_count += 1
            
        except Exception as e:
            logger.warning(f"Primary response failed: {e}, using fallback")
            self.fallback_count += 1
            
            # Try cached response
            response_text = await self._get_cached_response(message)
            
            if not response_text:
                # Final fallback to template
                response_text = self._get_template_response(message)
                
        # Store in memory for context
        if self.memory:
            await self.memory.store(
                user_id=hash(user) % 1000000,  # Simple user ID generation
                content=f"User: {message}\nBot: {response_text}",
                context={'type': 'chat', 'streamer': streamer_id},
                ttl=3600  # 1 hour
            )
            
        # Generate TTS if service available
        tts_data = None
        if self.tts and priority != 'low':
            try:
                # Apply personality voice selection
                voice = self._select_voice(personality) if personality else 'nova'
                tts_data = await self.tts.synthesize(response_text, voice)
            except Exception as e:
                logger.error(f"TTS generation failed: {e}")
                
        # Track performance
        elapsed = time.perf_counter() - start_time
        self.response_times.append(elapsed)
        
        # Keep only last 100 times
        if len(self.response_times) > 100:
            self.response_times = self.response_times[-100:]
            
        logger.info(f"Response generated in {elapsed:.2f}s")
        
        return {
            'text': response_text,
            'tts': tts_data,
            'user': user,
            'timestamp': datetime.now().isoformat(),
            'generation_time': elapsed,
            'method': 'primary' if self.success_count > self.fallback_count else 'fallback'
        }
        
    async def _build_context(self, user: str) -> Dict[str, Any]:
        """Build context from memory system.
        
        Retrieves recent interactions for context.
        """
        context = {
            'user': user,
            'timestamp': datetime.now().isoformat()
        }
        
        if self.memory:
            # Get user's recent memories
            user_id = hash(user) % 1000000
            memories = await self.memory.retrieve(user_id=user_id, limit=5)
            
            if memories:
                context['recent_messages'] = [m['content'] for m in memories]
                context['interaction_count'] = len(memories)
                
        return context
        
    async def _primary_response(
        self, 
        message: str, 
        context: dict,
        personality=None
    ) -> str:
        """Generate primary LLM response.
        
        Uses Thread 1's resilient HTTP client with circuit breaker.
        """
        if not self.http:
            # Fallback if HTTP client not available
            if personality and self.personality:
                return self.personality.generate_response(message, context, personality)
            else:
                raise Exception("No HTTP client available")
                
        # Build prompt
        prompt = self._build_llm_prompt(message, context, personality)
        
        # Call LLM through resilient client
        # This would use the actual endpoint from Thread 1
        response = await self.http.post(
            '/chat/completions',
            json={
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 150,
                'temperature': 0.7
            },
            timeout=5.0
        )
        
        if response and 'choices' in response:
            text = response['choices'][0]['message']['content']
            
            # Cache the response
            self._cache_response(message, text)
            
            return text
            
        raise Exception("Invalid LLM response")
        
    async def _get_cached_response(self, message: str) -> Optional[str]:
        """Get cached response if available.
        
        Uses fuzzy matching for similar messages.
        """
        # Clean up expired cache entries
        current_time = datetime.now()
        expired_keys = [
            key for key, timestamp in self.cache_timestamps.items()
            if current_time - timestamp > timedelta(seconds=self.cache_ttl)
        ]
        
        for key in expired_keys:
            del self.response_cache[key]
            del self.cache_timestamps[key]
            
        # Try exact match
        cache_key = self._get_cache_key(message)
        if cache_key in self.response_cache:
            logger.debug("Cache hit for exact message")
            return self.response_cache[cache_key]
            
        # Try fuzzy match (simple word overlap)
        message_words = set(message.lower().split())
        best_match = None
        best_score = 0
        
        for cached_msg, response in self.response_cache.items():
            cached_words = set(cached_msg.lower().split())
            overlap = len(message_words & cached_words)
            score = overlap / max(len(message_words), len(cached_words))
            
            if score > 0.7 and score > best_score:
                best_score = score
                best_match = response
                
        if best_match:
            logger.debug(f"Cache hit with fuzzy match (score: {best_score:.2f})")
            return best_match
            
        return None
        
    def _get_template_response(self, message: str) -> str:
        """Get template response as final fallback.
        
        Analyzes message type and returns appropriate template.
        """
        message_lower = message.lower()
        
        # Detect message type
        if any(greeting in message_lower for greeting in ['hi', 'hello', 'hey']):
            template_type = 'greeting'
        elif '?' in message:
            template_type = 'question'
        else:
            template_type = 'default'
            
        # Select random template
        import random
        templates = self.FALLBACK_TEMPLATES[template_type]
        response = random.choice(templates)
        
        logger.info(f"Using template response: {template_type}")
        return response
        
    def _cache_response(self, message: str, response: str):
        """Cache a successful response."""
        cache_key = self._get_cache_key(message)
        self.response_cache[cache_key] = response
        self.cache_timestamps[cache_key] = datetime.now()
        
        # Limit cache size
        if len(self.response_cache) > 100:
            # Remove oldest entry
            oldest_key = min(self.cache_timestamps, key=self.cache_timestamps.get)
            del self.response_cache[oldest_key]
            del self.cache_timestamps[oldest_key]
            
    def _get_cache_key(self, message: str) -> str:
        """Generate cache key for message."""
        # Normalize message for caching
        normalized = message.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()
        
    def _build_llm_prompt(self, message: str, context: dict, personality=None) -> str:
        """Build prompt for LLM with context and personality."""
        prompt_parts = []
        
        # Add personality instructions if available
        if personality:
            prompt_parts.append(f"Personality: {personality.tone}")
            prompt_parts.append(f"Energy level: {personality.energy}")
            
        # Add context
        if context.get('recent_messages'):
            prompt_parts.append("Recent conversation:")
            for msg in context['recent_messages'][-3:]:
                prompt_parts.append(f"  {msg}")
                
        # Add the actual message
        prompt_parts.append(f"\nUser says: {message}")
        prompt_parts.append("\nRespond appropriately:")
        
        return "\n".join(prompt_parts)
        
    def _select_voice(self, personality) -> str:
        """Select TTS voice based on personality."""
        if not personality:
            return 'nova'
            
        # Map personality traits to voices
        if personality.energy > 0.7:
            return 'alloy'  # Energetic voice
        elif personality.formality > 0.7:
            return 'onyx'  # Professional voice
        elif personality.humor_level > 0.7:
            return 'fable'  # Playful voice
        else:
            return 'nova'  # Balanced default
            
    def get_stats(self) -> dict:
        """Get response generator statistics."""
        avg_time = sum(self.response_times) / len(self.response_times) if self.response_times else 0
        
        return {
            'average_response_time': avg_time,
            'success_rate': self.success_count / max(1, self.success_count + self.fallback_count),
            'cache_size': len(self.response_cache),
            'fallback_count': self.fallback_count,
            'success_count': self.success_count
        }