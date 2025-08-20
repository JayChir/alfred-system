"""
Time MCP Server for Alfred System
Provides timezone utilities and time conversions
"""

from datetime import datetime
from typing import Optional
import pytz
from fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("alfred-time")

@mcp.tool()
def get_current_time(timezone: str = "UTC") -> str:
    """
    Get current time in a specific timezone
    
    Args:
        timezone: IANA timezone name (e.g., 'America/New_York', 'Europe/London')
    
    Returns:
        Current time as ISO 8601 string
    """
    try:
        tz = pytz.timezone(timezone)
        current_time = datetime.now(tz)
        return {
            "timezone": timezone,
            "current_time": current_time.isoformat(),
            "formatted": current_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "unix_timestamp": int(current_time.timestamp())
        }
    except pytz.exceptions.UnknownTimeZoneError:
        return {"error": f"Unknown timezone: {timezone}"}

@mcp.tool()
def convert_time(
    time: str,
    source_timezone: str = "UTC",
    target_timezone: str = "UTC"
) -> str:
    """
    Convert time between timezones
    
    Args:
        time: Time in ISO 8601 format or 'HH:MM' format
        source_timezone: Source IANA timezone
        target_timezone: Target IANA timezone
    
    Returns:
        Converted time information
    """
    try:
        source_tz = pytz.timezone(source_timezone)
        target_tz = pytz.timezone(target_timezone)
        
        # Parse the time
        if 'T' in time:  # ISO format
            dt = datetime.fromisoformat(time.replace('Z', '+00:00'))
        else:  # HH:MM format
            dt = datetime.strptime(time, "%H:%M")
            dt = dt.replace(year=datetime.now().year, 
                          month=datetime.now().month,
                          day=datetime.now().day)
        
        # Localize to source timezone if not already aware
        if dt.tzinfo is None:
            dt = source_tz.localize(dt)
        else:
            dt = dt.astimezone(source_tz)
        
        # Convert to target timezone
        converted = dt.astimezone(target_tz)
        
        return {
            "source": {
                "timezone": source_timezone,
                "time": dt.isoformat(),
                "formatted": dt.strftime("%Y-%m-%d %H:%M:%S %Z")
            },
            "target": {
                "timezone": target_timezone,
                "time": converted.isoformat(),
                "formatted": converted.strftime("%Y-%m-%d %H:%M:%S %Z")
            },
            "offset_hours": (converted.utcoffset().total_seconds() - 
                           dt.utcoffset().total_seconds()) / 3600
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def list_timezones(region: Optional[str] = None) -> list:
    """
    List available timezones, optionally filtered by region
    
    Args:
        region: Optional region filter (e.g., 'US', 'Europe', 'Asia')
    
    Returns:
        List of timezone names
    """
    all_timezones = pytz.all_timezones
    
    if region:
        filtered = [tz for tz in all_timezones if region.lower() in tz.lower()]
        return {
            "region": region,
            "count": len(filtered),
            "timezones": filtered[:50]  # Limit to 50 for readability
        }
    
    # Return common timezones if no filter
    common = [
        "UTC",
        "America/New_York",
        "America/Chicago", 
        "America/Denver",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Paris",
        "Asia/Tokyo",
        "Asia/Shanghai",
        "Australia/Sydney"
    ]
    
    return {
        "count": len(all_timezones),
        "common_timezones": common,
        "note": "Use region parameter to filter (e.g., 'US', 'Europe')"
    }

if __name__ == "__main__":
    # Run the FastMCP server with HTTP transport (Streamable HTTP)
    mcp.run(transport="http", host="0.0.0.0", port=8005)