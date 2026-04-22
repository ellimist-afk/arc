-- TalkBot Database Schema
-- PostgreSQL schema for memory, chat, and bot data

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For fuzzy text search

-- Drop existing tables if they exist (for clean setup)
DROP TABLE IF EXISTS chat_messages CASCADE;
DROP TABLE IF EXISTS memories CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS personalities CASCADE;
DROP TABLE IF EXISTS stream_sessions CASCADE;
DROP TABLE IF EXISTS audio_cache CASCADE;
DROP TABLE IF EXISTS bot_stats CASCADE;

-- Users table
CREATE TABLE users (
    user_id VARCHAR(100) PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    display_name VARCHAR(100),
    is_subscriber BOOLEAN DEFAULT FALSE,
    is_mod BOOLEAN DEFAULT FALSE,
    is_vip BOOLEAN DEFAULT FALSE,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Chat messages table
CREATE TABLE chat_messages (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(100) REFERENCES users(user_id) ON DELETE CASCADE,
    username VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,
    channel VARCHAR(100) NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_command BOOLEAN DEFAULT FALSE,
    sentiment_score FLOAT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Memories table (consolidated memory system)
CREATE TABLE memories (
    memory_id VARCHAR(100) PRIMARY KEY,
    user_id VARCHAR(100) REFERENCES users(user_id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL, -- 'general', 'personality_trait', 'preference', 'fact'
    content JSONB NOT NULL,
    importance FLOAT DEFAULT 0.5, -- 0-1 scale
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb,
    embedding VECTOR(1536), -- For semantic search if using embeddings
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP, -- For GDPR compliance
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Personalities table
CREATE TABLE personalities (
    id SERIAL PRIMARY KEY,
    streamer_id VARCHAR(100) NOT NULL,
    preset VARCHAR(50) NOT NULL, -- 'friendly', 'sassy', 'educational', 'chaotic', 'custom'
    traits JSONB NOT NULL, -- Store all trait values
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(streamer_id, preset)
);

-- Stream sessions table
CREATE TABLE stream_sessions (
    id SERIAL PRIMARY KEY,
    channel VARCHAR(100) NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    peak_viewers INTEGER DEFAULT 0,
    total_messages INTEGER DEFAULT 0,
    total_audio_plays INTEGER DEFAULT 0,
    unique_chatters INTEGER DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Audio cache metadata table
CREATE TABLE audio_cache (
    cache_key VARCHAR(100) PRIMARY KEY,
    text_hash VARCHAR(64) NOT NULL,
    original_text TEXT NOT NULL,
    voice_model VARCHAR(50) DEFAULT 'alloy',
    playback_speed FLOAT DEFAULT 1.0,
    file_path TEXT,
    file_size INTEGER,
    duration_ms INTEGER,
    play_count INTEGER DEFAULT 0,
    last_played TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '7 days'
);

-- Bot statistics table
CREATE TABLE bot_stats (
    id SERIAL PRIMARY KEY,
    stat_type VARCHAR(50) NOT NULL, -- 'performance', 'error', 'usage'
    stat_name VARCHAR(100) NOT NULL,
    stat_value JSONB NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id INTEGER REFERENCES stream_sessions(id) ON DELETE CASCADE
);

-- Create indexes for performance
CREATE INDEX idx_chat_messages_user_id ON chat_messages(user_id);
CREATE INDEX idx_chat_messages_timestamp ON chat_messages(timestamp DESC);
CREATE INDEX idx_chat_messages_channel ON chat_messages(channel);

CREATE INDEX idx_memories_user_id ON memories(user_id);
CREATE INDEX idx_memories_type ON memories(type);
CREATE INDEX idx_memories_timestamp ON memories(timestamp DESC);
CREATE INDEX idx_memories_importance ON memories(importance DESC);

-- GIN index for JSONB search
CREATE INDEX idx_memories_content_gin ON memories USING gin(content);
CREATE INDEX idx_chat_messages_metadata_gin ON chat_messages USING gin(metadata);

-- Trigram index for fuzzy text search
CREATE INDEX idx_memories_content_trgm ON memories USING gin(content::text gin_trgm_ops);
CREATE INDEX idx_chat_messages_message_trgm ON chat_messages USING gin(message gin_trgm_ops);

-- Update timestamp trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply update trigger to tables with updated_at
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_memories_updated_at BEFORE UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_personalities_updated_at BEFORE UPDATE ON personalities
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Function to clean up old data (GDPR compliance)
CREATE OR REPLACE FUNCTION cleanup_old_data(days_to_keep INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    -- Delete old chat messages
    DELETE FROM chat_messages 
    WHERE timestamp < CURRENT_TIMESTAMP - INTERVAL '1 day' * days_to_keep
    AND user_id NOT IN (SELECT user_id FROM users WHERE is_mod = TRUE OR is_vip = TRUE);
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    
    -- Delete expired memories
    DELETE FROM memories 
    WHERE expires_at < CURRENT_TIMESTAMP
    OR (timestamp < CURRENT_TIMESTAMP - INTERVAL '1 day' * days_to_keep 
        AND type != 'personality_trait');
    
    -- Delete old audio cache entries
    DELETE FROM audio_cache 
    WHERE expires_at < CURRENT_TIMESTAMP;
    
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Insert default personalities
INSERT INTO personalities (streamer_id, preset, traits, is_active) VALUES
('default', 'friendly', '{
    "humor": 70,
    "formality": 30,
    "enthusiasm": 80,
    "sarcasm": 10,
    "helpfulness": 90,
    "chattiness": 70,
    "creativity": 60,
    "empathy": 85,
    "assertiveness": 40,
    "curiosity": 70
}'::jsonb, TRUE),
('default', 'sassy', '{
    "humor": 80,
    "formality": 20,
    "enthusiasm": 60,
    "sarcasm": 85,
    "helpfulness": 60,
    "chattiness": 80,
    "creativity": 70,
    "empathy": 50,
    "assertiveness": 80,
    "curiosity": 60
}'::jsonb, FALSE),
('default', 'educational', '{
    "humor": 30,
    "formality": 70,
    "enthusiasm": 60,
    "sarcasm": 5,
    "helpfulness": 95,
    "chattiness": 50,
    "creativity": 40,
    "empathy": 70,
    "assertiveness": 60,
    "curiosity": 90
}'::jsonb, FALSE),
('default', 'chaotic', '{
    "humor": 90,
    "formality": 10,
    "enthusiasm": 95,
    "sarcasm": 60,
    "helpfulness": 50,
    "chattiness": 90,
    "creativity": 95,
    "empathy": 40,
    "assertiveness": 70,
    "curiosity": 80
}'::jsonb, FALSE);