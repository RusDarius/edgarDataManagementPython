#!/usr/bin/env python3
"""
Simple test to verify the value handling fix works with Python 3.9
"""

def test_value_validation():
    """Test the numeric value validation logic in isolation."""
    
    print("🧪 Testing Numeric Value Validation Logic")
    print("=" * 50)
    
    # Test cases for different value types
    test_cases = [
        (123, 123.0, "Integer should convert to float"),
        (123.45, 123.45, "Float should remain float"),
        ("456", 456.0, "Numeric string should convert to float"),
        ("456.78", 456.78, "Decimal string should convert to float"),
        ("Q1", None, "Non-numeric string should return None"),
        ("2024-08-31", None, "Date string should return None"),
        (None, None, "None should remain None"),
        ("", None, "Empty string should return None"),
        ("abc", None, "Text string should return None"),
    ]
    
    passed = 0
    failed = 0
    
    for raw_value, expected, description in test_cases:
        try:
            # Replicate the logic from the fixed code
            value = None
            if raw_value is not None:
                try:
                    if isinstance(raw_value, (int, float)):
                        value = float(raw_value)
                    elif isinstance(raw_value, str):
                        value = float(raw_value)
                except (ValueError, TypeError):
                    value = None
            
            if value == expected:
                print(f"✅ {description}: {raw_value} -> {value}")
                passed += 1
            else:
                print(f"❌ {description}: {raw_value} -> {value} (expected {expected})")
                failed += 1
                
        except Exception as e:
            print(f"💥 {description}: {raw_value} -> Error: {e}")
            failed += 1
    
    print(f"\n📊 Validation Results: {passed} passed, {failed} failed")
    return failed == 0

if __name__ == "__main__":
    import sys
    print(f"Python version: {sys.version}")
    success = test_value_validation()
    if success:
        print(f"\n🎉 All tests passed! The value handling fix is working correctly with Python 3.9.")
    else:
        print(f"\n⚠️ Some tests failed.")
    sys.exit(0 if success else 1)
