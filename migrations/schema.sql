-- TalkBot Database Schema
-- Version: 1.0.0
-- Description: Optimized schema for TalkBot with performance improvements

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm"; -- For text search optimization

-- Drop existing tables if doing fresh install (comment out for production)
-- DROP TABLE IF EXISTS messages CASCADE;
-- DROP TABLE IF EXISTS users CASCADE;
-- DROP TABLE IF EXISTS personalities CASCADE;
-- DROP TABLE IF EXISTS audio_cache CASCADE;
-- DROP TABLE IF EXISTS bot_state CASCADE;
-- DROP TABLE IF EXISTS raid_events CASCADE;
-- DROP TABLE IF EXISTS metrics CASCADE;

-- Users table with optimizations
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(255) PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    display_name VARCHAR(255),
    first_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER DEFAULT 0,
    is_subscriber BOOLEAN DEFAULT FALSE,
    is_moderator BOOLEAN DEFAULT FALSE,
    is_vip BOOLEAN DEFAULT FALSE,
    is_broadcaster BOOLEAN DEFAULT FALSE,
    -- Metadata for personalization
    preferred_topics TEXT[],
    interaction_style VARCHAR(50), -- 'casual', 'formal', 'playful', etc.
    last_raid_date TIMESTAMP WITH TIME ZONE,
    total_raids INTEGER DEFAULT 0,
    -- Indexes for performance
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for user queries
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_users_message_count ON users(message_count DESC);

-- Messages table with partitioning support
CREATE TABLE IF NOT EXISTS messages (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    username VARCHAR(255) NOT NULL,
    channel VARCHAR(255) NOT NULL,
    text TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    -- Message metadata
    is_command BOOLEAN DEFAULT FALSE,
    is_mention BOOLEAN DEFAULT FALSE,
    is_voice BOOLEAN DEFAULT FALSE,
    sentiment_score FLOAT, -- -1.0 to 1.0
    response_generated BOOLEAN DEFAULT FALSE,
    response_text TEXT,
    response_time_ms INTEGER,
    -- Context for better responses
    context_type VARCHAR(50), -- 'chat', 'voice', 'raid', 'command'
    priority VARCHAR(20) DEFAULT 'normal', -- 'high', 'normal', 'low'
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Indexes for message queries (optimized for <100ms context building)
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_user_timestamp ON messages(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_channel_timestamp ON messages(channel, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_is_mention ON messages(is_mention) WHERE is_mention = true;
CREATE INDEX IF NOT EXISTS idx_messages_is_voice ON messages(is_voice) WHERE is_voice = true;
-- Full text search index for message content
CREATE INDEX IF NOT EXISTS idx_messages_text_search ON messages USING gin(to_tsvector('english', text));

-- Personalities table
CREATE TABLE IF NOT EXISTS personalities (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    description TEXT,
    system_prompt TEXT NOT NULL,
    temperature FLOAT DEFAULT 0.7,
    max_tokens INTEGER DEFAULT 150,
    -- Behavior settings
    response_style TEXT,
    topics_of_interest TEXT[],
    avoided_topics TEXT[],
    catchphrases TEXT[],
    -- Voice settings
    voice_model VARCHAR(50) DEFAULT 'nova',
    voice_speed FLOAT DEFAULT 1.0,
    voice_pitch FLOAT DEFAULT 1.0,
    -- Metadata
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Audio cache table for TTS optimization
CREATE TABLE IF NOT EXISTS audio_cache (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    text_hash VARCHAR(64) UNIQUE NOT NULL, -- SHA256 hash of text
    text TEXT NOT NULL,
    audio_data BYTEA, -- Store small files directly
    audio_url TEXT, -- Or store path/URL for larger files
    voice_model VARCHAR(50) NOT NULL,
    voice_settings JSONB, -- Speed, pitch, etc.
    file_size_bytes INTEGER,
    duration_ms INTEGER,
    hit_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE -- For cache expiration
);

-- Indexes for audio cache
CREATE INDEX IF NOT EXISTS idx_audio_cache_text_hash ON audio_cache(text_hash);
CREATE INDEX IF NOT EXISTS idx_audio_cache_hit_count ON audio_cache(hit_count DESC);
CREATE INDEX IF NOT EXISTS idx_audio_cache_last_accessed ON audio_cache(last_accessed DESC);
CREATE INDEX IF NOT EXISTS idx_audio_cache_expires ON audio_cache(expires_at) WHERE expires_at IS NOT NULL;

-- Bot state table (single source of truth)
CREATE TABLE IF NOT EXISTS bot_state (
    streamer_id VARCHAR(255) PRIMARY KEY,
    is_running BOOLEAN DEFAULT FALSE,
    voice_enabled BOOLEAN DEFAULT TRUE,
    tts_enabled BOOLEAN DEFAULT TRUE,
    response_cooldown INTEGER DEFAULT 5, -- seconds
    personality_preset VARCHAR(255),
    primary_model VARCHAR(50) DEFAULT 'gpt-4',
    fallback_model VARCHAR(50) DEFAULT 'gpt-3.5-turbo',
    -- Feature flags
    raider_welcome_enabled BOOLEAN DEFAULT FALSE,
    raider_analysis_depth VARCHAR(20) DEFAULT 'basic', -- 'basic' or 'full'
    dead_air_prevention_enabled BOOLEAN DEFAULT TRUE,
    dead_air_threshold_seconds INTEGER DEFAULT 60,
    -- Performance settings
    max_context_messages INTEGER DEFAULT 50,
    context_time_window_minutes INTEGER DEFAULT 30,
    audio_cache_size_mb INTEGER DEFAULT 500,
    -- Metrics
    total_messages_processed BIGINT DEFAULT 0,
    total_audio_generated BIGINT DEFAULT 0,
    avg_response_time_ms FLOAT,
    uptime_seconds BIGINT DEFAULT 0,
    last_health_check TIMESTAMP WITH TIME ZONE,
    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (personality_preset) REFERENCES personalities(name) ON DELETE SET NULL
);

-- Raid events table for raider welcome feature
CREATE TABLE IF NOT EXISTS raid_events (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    raider_user_id VARCHAR(255) NOT NULL,
    raider_username VARCHAR(255) NOT NULL,
    raider_display_name VARCHAR(255),
    viewer_count INTEGER NOT NULL,
    raid_timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    -- Welcome generation
    welcome_generated BOOLEAN DEFAULT FALSE,
    welcome_message TEXT,
    vod_analyzed BOOLEAN DEFAULT FALSE,
    vod_summary TEXT,
    game_played VARCHAR(255),
    stream_title TEXT,
    -- Response metrics
    processing_time_ms INTEGER,
    tts_queued BOOLEAN DEFAULT FALSE,
    
    FOREIGN KEY (raider_user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Indexes for raid events
CREATE INDEX IF NOT EXISTS idx_raid_events_timestamp ON raid_events(raid_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_raid_events_raider ON raid_events(raider_user_id);

-- Metrics table for performance tracking
CREATE TABLE IF NOT EXISTS metrics (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    metric_name VARCHAR(100) NOT NULL,
    metric_value FLOAT NOT NULL,
    metric_type VARCHAR(50) NOT NULL, -- 'counter', 'gauge', 'histogram'
    labels JSONB, -- Additional metadata
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for metrics queries
CREATE INDEX IF NOT EXISTS idx_metrics_name_timestamp ON metrics(metric_name, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp DESC);
-- Partial index for recent metrics (last 24 hours)
CREATE INDEX IF NOT EXISTS idx_metrics_recent 
    ON metrics(metric_name, timestamp DESC) 
    WHERE timestamp > CURRENT_TIMESTAMP - INTERVAL '24 hours';

-- Create update trigger for updated_at columns
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply update trigger to relevant tables
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_personalities_updated_at BEFORE UPDATE ON personalities
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_bot_state_updated_at BEFORE UPDATE ON bot_state
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Performance optimization: Create a materialized view for context building
CREATE MATERIALIZED VIEW IF NOT EXISTS recent_context AS
SELECT 
    m.user_id,
    m.username,
    m.channel,
    m.text,
    m.timestamp,
    m.is_mention,
    m.sentiment_score,
    u.message_count,
    u.is_subscriber,
    u.is_moderator,
    u.is_vip
FROM messages m
JOIN users u ON m.user_id = u.user_id
WHERE m.timestamp > CURRENT_TIMESTAMP - INTERVAL '1 hour'
ORDER BY m.timestamp DESC;

-- Index the materialized view
CREATE INDEX IF NOT EXISTS idx_recent_context_user ON recent_context(user_id);
CREATE INDEX IF NOT EXISTS idx_recent_context_timestamp ON recent_context(timestamp DESC);

-- Refresh the materialized view periodically (call this from application)
-- REFRESH MATERIALIZED VIEW CONCURRENTLY recent_context;

-- Grant permissions (adjust as needed)
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO streambot_user;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO streambot_user;
-- GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO streambot_user;

-- Insert default personality
INSERT INTO personalities (name, description, system_prompt, temperature, max_tokens, voice_model)
VALUES (
    'default',
    'Default TalkBot personality',
    'You are a helpful and friendly Twitch chat bot. Be concise, engaging, and appropriate for a streaming audience.',
    0.7,
    150,
    'nova'
) ON CONFLICT (name) DO NOTHING;

-- Insert default bot state
INSERT INTO bot_state (streamer_id, personality_preset)
VALUES ('default', 'default')
ON CONFLICT (streamer_id) DO NOTHING;

-- Create performance statistics function
CREATE OR REPLACE FUNCTION get_performance_stats()
RETURNS TABLE(
    total_users BIGINT,
    total_messages BIGINT,
    messages_last_hour BIGINT,
    avg_response_time_ms FLOAT,
    cache_hit_rate FLOAT,
    active_personalities BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        (SELECT COUNT(*) FROM users)::BIGINT as total_users,
        (SELECT COUNT(*) FROM messages)::BIGINT as total_messages,
        (SELECT COUNT(*) FROM messages WHERE timestamp > CURRENT_TIMESTAMP - INTERVAL '1 hour')::BIGINT as messages_last_hour,
        (SELECT AVG(response_time_ms) FROM messages WHERE response_time_ms IS NOT NULL)::FLOAT as avg_response_time_ms,
        (SELECT CASE 
            WHEN SUM(hit_count) > 0 THEN 
                SUM(hit_count)::FLOAT / (COUNT(*) + SUM(hit_count))::FLOAT 
            ELSE 0 
        END FROM audio_cache)::FLOAT as cache_hit_rate,
        (SELECT COUNT(*) FROM personalities WHERE is_active = true)::BIGINT as active_personalities;
END;
$$ LANGUAGE plpgsql;

-- Version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version VARCHAR(20) PRIMARY KEY,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

INSERT INTO schema_version (version, description)
VALUES ('1.0.0', 'Initial TalkBot schema with performance optimizations')
ON CONFLICT (version) DO NOTHING;