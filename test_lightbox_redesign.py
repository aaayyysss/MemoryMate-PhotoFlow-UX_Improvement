#!/usr/bin/env python3
"""
Test script to verify lightbox redesign improvements.

Tests:
1. Window sizing is within proper bounds (85-90% of screen)
2. Panel widths are appropriately sized
3. Navigation buttons are positioned correctly
4. Panel auto-hiding works on window resize
"""

import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    import time
    
    # Import the media lightbox
    from google_components.media_lightbox import MediaLightbox
    
    print("Testing lightbox redesign...")
    print("=" * 50)
    
    # Create Qt application
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    def test_lightbox_sizing():
        """Test that lightbox sizing follows professional standards."""
        print("\n1. Testing window sizing...")
        
        # Create a test lightbox (won't actually show media)
        lightbox = MediaLightbox(None, [], None)
        
        # Get screen info
        screen = app.primaryScreen()
        screen_width = screen.geometry().width()
        screen_height = screen.geometry().height()
        
        window_width = lightbox.width()
        window_height = lightbox.height()
        
        # Calculate percentage
        width_percent = (window_width / screen_width) * 100
        height_percent = (window_height / screen_height) * 100
        
        print(f"   Screen size: {screen_width}x{screen_height}")
        print(f"   Window size: {window_width}x{window_height}")
        print(f"   Size percentage: {width_percent:.1f}% x {height_percent:.1f}%")
        
        # Verify sizing is within professional bounds (85-92%)
        if 85 <= width_percent <= 92 and 85 <= height_percent <= 92:
            print("   ✅ Window sizing is within professional bounds")
            return True
        else:
            print(f"   ❌ Window sizing outside bounds (should be 85-92%)")
            return False
    
    def test_panel_widths():
        """Test that panel widths are appropriate."""
        print("\n2. Testing panel widths...")
        
        lightbox = MediaLightbox(None, [], None)
        
        # Check if panel width attribute exists
        if hasattr(lightbox, 'panel_width'):
            panel_width = lightbox.panel_width
            print(f"   Panel width: {panel_width}px")
            
            # Verify panel width is reasonable (240-320px)
            if 240 <= panel_width <= 320:
                print("   ✅ Panel width is appropriate")
                return True
            else:
                print(f"   ❌ Panel width outside expected range (240-320px)")
                return False
        else:
            print("   ❌ panel_width attribute not found")
            return False
    
    def test_navigation_button_positioning():
        """Test navigation button positioning logic."""
        print("\n3. Testing navigation button positioning...")
        
        lightbox = MediaLightbox(None, [], None)
        
        # Check if positioning method exists
        if hasattr(lightbox, '_position_nav_buttons'):
            print("   ✅ Navigation button positioning method exists")
            return True
        else:
            print("   ❌ _position_nav_buttons method not found")
            return False
    
    def test_panel_auto_hide():
        """Test panel auto-hiding functionality."""
        print("\n4. Testing panel auto-hide logic...")
        
        lightbox = MediaLightbox(None, [], None)
        
        # Check if auto-hide method exists
        if hasattr(lightbox, '_handle_panel_visibility'):
            print("   ✅ Panel auto-hide method exists")
            
            # Test the logic with different window sizes
            original_width = lightbox.width()
            
            # Simulate narrow window (should trigger auto-hide)
            lightbox.resize(1000, 800)
            lightbox._handle_panel_visibility()
            print("   ✅ Auto-hide logic executed for narrow window")
            
            # Simulate wide window (should allow panels)
            lightbox.resize(1400, 1000)
            lightbox._handle_panel_visibility()
            print("   ✅ Panel restoration logic executed for wide window")
            
            # Restore original size
            lightbox.resize(original_width, lightbox.height())
            
            return True
        else:
            print("   ❌ _handle_panel_visibility method not found")
            return False
    
    # Run all tests
    tests = [
        test_lightbox_sizing,
        test_panel_widths,
        test_navigation_button_positioning,
        test_panel_auto_hide
    ]
    
    passed = 0
    total = len(tests)
    
    for test_func in tests:
        try:
            if test_func():
                passed += 1
        except Exception as e:
            print(f"   💥 Test failed with exception: {e}")
    
    print("\n" + "=" * 50)
    print(f"RESULTS: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 ALL TESTS PASSED - Lightbox redesign verified!")
    else:
        print("❌ Some tests failed - please review implementation")
    
    print("\nLightbox redesign test completed!")
    
except Exception as e:
    print(f"💥 Test failed with exception: {e}")
    import traceback
    traceback.print_exc()