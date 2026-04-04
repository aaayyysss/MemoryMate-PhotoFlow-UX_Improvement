# utils/dpi_helper.py
"""
DPI and Resolution Helper for Adaptive UI Sizing
Provides utilities for creating DPI-aware, resolution-adaptive UIs
"""

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QRect


class DPIHelper:
    """Helper class for DPI-aware, resolution-adaptive UI sizing."""
    
    @staticmethod
    def get_screen_info():
        """
        Get comprehensive screen information.
        
        Returns:
            dict: Screen information including:
                - width: Logical screen width
                - height: Logical screen height
                - dpi_scale: Windows DPI scale factor (1.0, 1.25, 1.5, 2.0, etc.)
                - category: Screen category ('small', 'hd', 'fhd', '4k')
                - is_high_dpi: Whether DPI scaling is enabled (>1.0)
        """
        screen = QApplication.primaryScreen()
        geometry = screen.geometry()
        available = screen.availableGeometry()
        dpi_scale = screen.devicePixelRatio()
        
        width = geometry.width()
        height = geometry.height()
        
        # Categorize screen size
        if width >= 2560:
            category = '4k'
        elif width >= 1920:
            category = 'fhd'
        elif width >= 1366:
            category = 'hd'
        else:
            category = 'small'
        
        return {
            'width': width,
            'height': height,
            'available_width': available.width(),
            'available_height': available.height(),
            'dpi_scale': dpi_scale,
            'category': category,
            'is_high_dpi': dpi_scale > 1.0,
            'taskbar_height': height - available.height(),
        }
    
    @staticmethod
    def get_adaptive_margin():
        """
        Get adaptive window margin based on screen size.
        
        Larger screens get larger margins for better aesthetics.
        
        Returns:
            int: Margin in logical pixels
        """
        info = DPIHelper.get_screen_info()
        
        if info['category'] == '4k':
            return 80
        elif info['category'] == 'fhd':
            return 60
        elif info['category'] == 'hd':
            return 40
        else:  # small
            return 20
    
    @staticmethod
    def get_adaptive_dialog_size(base_width, base_height, max_screen_percent=0.9):
        """
        Get adaptive dialog size based on screen resolution.
        
        Args:
            base_width: Base width for Full HD screens
            base_height: Base height for Full HD screens
            max_screen_percent: Maximum percentage of screen to use (default 0.9)
        
        Returns:
            tuple: (width, height) in logical pixels
        """
        info = DPIHelper.get_screen_info()
        
        # Scale based on screen category
        if info['category'] == '4k':
            scale_factor = 1.33  # 33% larger on 4K
        elif info['category'] == 'fhd':
            scale_factor = 1.0   # Base size
        elif info['category'] == 'hd':
            scale_factor = 0.85  # 15% smaller on HD
        else:  # small
            scale_factor = 0.70  # 30% smaller on small screens
        
        width = int(base_width * scale_factor)
        height = int(base_height * scale_factor)
        
        # Ensure doesn't exceed screen size
        max_width = int(info['available_width'] * max_screen_percent)
        max_height = int(info['available_height'] * max_screen_percent)
        
        width = min(width, max_width)
        height = min(height, max_height)
        
        return width, height
    
    @staticmethod
    def get_adaptive_font_size(base_size=10):
        """
        Get adaptive font size based on DPI scaling.
        
        Args:
            base_size: Base font size in points (default 10)
        
        Returns:
            int: Adjusted font size in points
        """
        info = DPIHelper.get_screen_info()
        
        # On high-DPI screens, font size is automatically scaled by Qt
        # But we may want to adjust slightly for better readability
        if info['is_high_dpi']:
            # High-DPI screens: keep base size (Qt handles scaling)
            return base_size
        else:
            # Standard DPI: use base size
            return base_size
    
    @staticmethod
    def get_adaptive_icon_size():
        """
        Get adaptive icon size based on screen resolution.
        
        Returns:
            int: Icon size in logical pixels
        """
        info = DPIHelper.get_screen_info()
        
        if info['category'] == '4k':
            return 32  # Larger icons on 4K
        elif info['category'] == 'fhd':
            return 24  # Standard icons on Full HD
        elif info['category'] == 'hd':
            return 20  # Slightly smaller on HD
        else:  # small
            return 16  # Compact icons on small screens
    
    @staticmethod
    def get_centered_geometry(width, height):
        """
        Get centered window geometry.
        
        Args:
            width: Desired window width
            height: Desired window height
        
        Returns:
            QRect: Centered window geometry
        """
        screen = QApplication.primaryScreen()
        screen_geometry = screen.geometry()
        
        x = (screen_geometry.width() - width) // 2
        y = (screen_geometry.height() - height) // 2
        
        return QRect(x, y, width, height)
    
    @staticmethod
    def scale_size(size, min_scale=0.5, max_scale=2.0):
        """
        Scale a size value based on screen DPI and resolution.
        
        Useful for spacing, padding, margins, etc.
        
        Args:
            size: Base size value
            min_scale: Minimum scale factor (default 0.5)
            max_scale: Maximum scale factor (default 2.0)
        
        Returns:
            int: Scaled size
        """
        info = DPIHelper.get_screen_info()
        
        # Combine DPI scale and screen category for adaptive scaling
        if info['category'] == '4k':
            scale = 1.5
        elif info['category'] == 'fhd':
            scale = 1.0
        elif info['category'] == 'hd':
            scale = 0.85
        else:  # small
            scale = 0.7
        
        # Apply DPI scaling on top
        scale *= info['dpi_scale']
        
        # Clamp to min/max
        scale = max(min_scale, min(scale, max_scale))
        
        return int(size * scale)
    
    @staticmethod
    def print_screen_info():
        """Print screen information for debugging."""
        info = DPIHelper.get_screen_info()
        print("=" * 60)
        print("SCREEN INFORMATION (DPI-Aware)")
        print("=" * 60)
        print(f"Resolution:      {info['width']}x{info['height']}")
        print(f"Available:       {info['available_width']}x{info['available_height']}")
        print(f"Category:        {info['category'].upper()}")
        print(f"DPI Scale:       {info['dpi_scale']}x ({int(info['dpi_scale']*100)}%)")
        print(f"High-DPI:        {'Yes' if info['is_high_dpi'] else 'No'}")
        print(f"Taskbar Height:  {info['taskbar_height']}px")
        print(f"Adaptive Margin: {DPIHelper.get_adaptive_margin()}px")
        print(f"Adaptive Icons:  {DPIHelper.get_adaptive_icon_size()}px")
        print("=" * 60)


# Convenience functions for common use cases
def get_adaptive_window_size(base_width, base_height, margin_percent=0.05):
    """
    Get adaptive window size with margins.
    
    Args:
        base_width: Base width percentage (0.0-1.0) or absolute pixels (>1)
        base_height: Base height percentage (0.0-1.0) or absolute pixels (>1)
        margin_percent: Margin as percentage of screen (default 0.05 = 5%)
    
    Returns:
        tuple: (width, height, x, y)
    """
    screen = QApplication.primaryScreen()
    available = screen.availableGeometry()
    
    # Calculate width
    if base_width <= 1.0:
        width = int(available.width() * base_width)
    else:
        width = int(base_width)
    
    # Calculate height
    if base_height <= 1.0:
        height = int(available.height() * base_height)
    else:
        height = int(base_height)
    
    # Apply margins
    margin = int(min(available.width(), available.height()) * margin_percent)
    width = min(width, available.width() - 2 * margin)
    height = min(height, available.height() - 2 * margin)
    
    # Center on screen
    x = (available.width() - width) // 2 + available.x()
    y = (available.height() - height) // 2 + available.y()
    
    return width, height, x, y


def get_screen_scale_factor():
    """
    Get combined screen scale factor (DPI + resolution).
    
    Returns:
        float: Scale factor (0.7 to 2.0)
    """
    return DPIHelper.get_screen_info()['dpi_scale']


# Export main classes and functions
__all__ = [
    'DPIHelper',
    'get_adaptive_window_size',
    'get_screen_scale_factor',
]
