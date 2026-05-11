"""Optimized caching for faster dashboard performance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Optional


class CacheManager:
    """Intelligent cache with TTL and smart invalidation."""
    
    def __init__(self, ttl_seconds: int = 300):
        self.cache: dict[str, dict[str, Any]] = {}
        self.ttl_seconds = ttl_seconds
    
    def set(self, key: str, value: Any, ttl_override: Optional[int] = None) -> None:
        """Store value with TTL."""
        self.cache[key] = {
            "value": value,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_override or self.ttl_seconds),
        }
    
    def get(self, key: str) -> Optional[Any]:
        """Get value if not expired."""
        if key not in self.cache:
            return None
        
        entry = self.cache[key]
        if datetime.now(timezone.utc) > entry["expires_at"]:
            del self.cache[key]
            return None
        
        return entry["value"]
    
    def is_expired(self, key: str) -> bool:
        """Check if cache entry is expired."""
        return self.get(key) is None
    
    def clear(self) -> None:
        """Clear all cache."""
        self.cache.clear()
    
    def clear_key(self, key: str) -> None:
        """Clear specific key."""
        if key in self.cache:
            del self.cache[key]


class MetadataCache:
    """Caches processed resource metadata for fast queries."""
    
    def __init__(self):
        self.metadata: dict[str, Any] = {}
        self.last_updated = None
    
    def update(self, resources: list[dict[str, Any]]) -> None:
        """Build metadata index from resources."""
        if not resources:
            return
        
        self.metadata = {
            "total_count": len(resources),
            "by_type": {},
            "by_project": {},
            "by_location": {},
            "total_cost": 0.0,
        }
        
        for resource in resources:
            asset_type = resource.get("asset_type", "Unknown")
            project = resource.get("project", "Unknown")
            location = resource.get("location", "Global")
            cost = float(resource.get("estimated_monthly_cost", 0))
            
            # By type
            if asset_type not in self.metadata["by_type"]:
                self.metadata["by_type"][asset_type] = 0
            self.metadata["by_type"][asset_type] += 1
            
            # By project
            if project not in self.metadata["by_project"]:
                self.metadata["by_project"][project] = 0
            self.metadata["by_project"][project] += 1
            
            # By location
            if location not in self.metadata["by_location"]:
                self.metadata["by_location"][location] = 0
            self.metadata["by_location"][location] += 1
            
            # Total cost
            self.metadata["total_cost"] += cost
        
        self.last_updated = datetime.now(timezone.utc)
    
    def get_metadata(self) -> dict[str, Any]:
        """Get cached metadata."""
        return self.metadata.copy()
