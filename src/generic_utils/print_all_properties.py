# Add src to sys.path to allow imports
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generic_utils.accept_utf8_encoding import accept_utf8_encoding

accept_utf8_encoding()


def print_all_properties(
    obj, name="object", max_depth=3, current_depth=0, visited=None, show_values=True
):
    """
    Recursively print all properties of an object in a nested format.

    Args:
        obj: The object to inspect
        name: Name/label for the object
        max_depth: Maximum depth to traverse (default: 3)
        current_depth: Current recursion depth (internal)
        visited: Set of visited object IDs to avoid circular references (internal)
        show_values: Whether to show property values (default: True)

    Example:
        # For XBRL fact inspection:
        print_all_properties(model_xbrl.facts[0], "first_fact", max_depth=4)

        # For context inspection:
        print_all_properties(model_xbrl.facts[0].context, "fact_context", max_depth=3)

        # For period inspection:
        print_all_properties(model_xbrl.facts[0].context.period, "period", max_depth=2)
    """
    if visited is None:
        visited = set()

    # Avoid infinite recursion with circular references
    obj_id = id(obj)
    if obj_id in visited:
        print(f"{'  ' * current_depth}🔄 {name}: <CIRCULAR_REFERENCE>")
        return

    if current_depth >= max_depth:
        print(f"{'  ' * current_depth}⚠️ {name}: <MAX_DEPTH_REACHED>")
        return

    visited.add(obj_id)

    try:
        # Print object header
        obj_type = type(obj).__name__
        if show_values and hasattr(obj, "__str__") and current_depth == 0:
            try:
                obj_str = str(obj)
                if len(obj_str) > 100:
                    obj_str = obj_str[:100] + "..."
                print(f"{'  ' * current_depth}📋 {name} ({obj_type}): {obj_str}")
            except:
                print(f"{'  ' * current_depth}📋 {name} ({obj_type})")
        else:
            print(f"{'  ' * current_depth}📋 {name} ({obj_type})")

        # Get all attributes
        try:
            attributes = dir(obj)
        except:
            print(f"{'  ' * current_depth}  ❌ Cannot get attributes")
            return

        # Filter and categorize attributes
        public_attrs = []
        private_attrs = []
        methods = []
        properties = []

        for attr_name in attributes:
            if attr_name.startswith("__"):
                continue  # Skip dunder methods

            try:
                attr_value = getattr(obj, attr_name)

                if callable(attr_value):
                    methods.append(attr_name)
                elif attr_name.startswith("_"):
                    private_attrs.append((attr_name, attr_value))
                else:
                    public_attrs.append((attr_name, attr_value))

            except Exception as e:
                # Some attributes might not be accessible
                properties.append((attr_name, f"<ERROR: {e}>"))

        # Print public attributes (most important)
        if public_attrs:
            print(f"{'  ' * (current_depth + 1)}🔸 Public Attributes:")
            for attr_name, attr_value in public_attrs:
                _print_attribute(
                    attr_name,
                    attr_value,
                    current_depth + 2,
                    max_depth,
                    visited,
                    show_values,
                )

        # Print properties that had errors
        if properties:
            print(f"{'  ' * (current_depth + 1)}⚠️ Properties with Access Issues:")
            for attr_name, error_msg in properties:
                print(f"{'  ' * (current_depth + 2)}{attr_name}: {error_msg}")

        # Print methods (condensed)
        if methods and current_depth < 2:  # Only show methods at shallow depth
            method_names = [m for m in methods if not m.startswith("_")][
                :10
            ]  # Limit to first 10
            if method_names:
                print(
                    f"{'  ' * (current_depth + 1)}🔧 Methods: {', '.join(method_names)}"
                )
                if len(methods) > 10:
                    print(
                        f"{'  ' * (current_depth + 1)}   ... and {len(methods) - 10} more methods"
                    )

        # Print some private attributes if at shallow depth
        if private_attrs and current_depth < 2:
            important_private = [
                (n, v)
                for n, v in private_attrs
                if any(
                    keyword in n.lower()
                    for keyword in ["date", "time", "period", "value", "context", "id"]
                )
            ][:5]
            if important_private:
                print(f"{'  ' * (current_depth + 1)}🔹 Important Private Attributes:")
                for attr_name, attr_value in important_private:
                    _print_attribute(
                        attr_name,
                        attr_value,
                        current_depth + 2,
                        max_depth,
                        visited,
                        show_values,
                    )

    except Exception as e:
        print(f"{'  ' * current_depth}❌ Error inspecting {name}: {e}")

    finally:
        visited.remove(obj_id)


def _print_attribute(
    attr_name, attr_value, current_depth, max_depth, visited, show_values
):
    """Helper function to print individual attributes."""
    attr_type = type(attr_value).__name__

    # Handle None values
    if attr_value is None:
        print(f"{'  ' * current_depth}{attr_name}: None")
        return

    # Handle primitive types
    if isinstance(attr_value, (str, int, float, bool)):
        if show_values:
            display_value = str(attr_value)
            if len(display_value) > 80:
                display_value = display_value[:80] + "..."
            print(f"{'  ' * current_depth}{attr_name} ({attr_type}): {display_value}")
        else:
            print(f"{'  ' * current_depth}{attr_name} ({attr_type})")
        return

    # Handle collections
    if isinstance(attr_value, (list, tuple, set)):
        print(
            f"{'  ' * current_depth}{attr_name} ({attr_type}): [{len(attr_value)} items]"
        )
        if len(attr_value) > 0 and current_depth < max_depth:
            # Show first few items
            for i, item in enumerate(list(attr_value)[:3]):
                print_all_properties(
                    item, f"[{i}]", max_depth, current_depth + 1, visited, show_values
                )
            if len(attr_value) > 3:
                print(
                    f"{'  ' * (current_depth + 1)}... and {len(attr_value) - 3} more items"
                )
        return

    # Handle dictionaries
    if isinstance(attr_value, dict):
        print(
            f"{'  ' * current_depth}{attr_name} ({attr_type}): {{{len(attr_value)} keys}}"
        )
        if len(attr_value) > 0 and current_depth < max_depth:
            for i, (key, value) in enumerate(list(attr_value.items())[:3]):
                print_all_properties(
                    value,
                    f"['{key}']",
                    max_depth,
                    current_depth + 1,
                    visited,
                    show_values,
                )
            if len(attr_value) > 3:
                print(
                    f"{'  ' * (current_depth + 1)}... and {len(attr_value) - 3} more keys"
                )
        return

    # Handle complex objects
    if hasattr(attr_value, "__dict__") or hasattr(attr_value, "__slots__"):
        print(f"{'  ' * current_depth}{attr_name} ({attr_type}):")
        if current_depth < max_depth:
            print_all_properties(
                attr_value,
                attr_name,
                max_depth,
                current_depth + 1,
                visited,
                show_values,
            )
        return

    # Handle other types
    try:
        if show_values:
            display_value = str(attr_value)
            if len(display_value) > 80:
                display_value = display_value[:80] + "..."
            print(f"{'  ' * current_depth}{attr_name} ({attr_type}): {display_value}")
        else:
            print(f"{'  ' * current_depth}{attr_name} ({attr_type})")
    except:
        print(
            f"{'  ' * current_depth}{attr_name} ({attr_type}): <CANNOT_CONVERT_TO_STRING>"
        )


def print_xbrl_fact_structure(fact, max_depth=4):
    """
    Convenience function specifically for XBRL facts.

    Args:
        fact: An XBRL fact object (e.g., model_xbrl.facts[0])
        max_depth: Maximum depth to traverse

    Example:
        print_xbrl_fact_structure(model_xbrl.facts[0])
    """
    print("🔍 XBRL FACT STRUCTURE ANALYSIS")
    print("=" * 60)
    print_all_properties(fact, "xbrl_fact", max_depth=max_depth)

    # Also specifically examine context and period if available
    if hasattr(fact, "context") and fact.context is not None:
        print("\n" + "=" * 60)
        print("🕐 CONTEXT ANALYSIS")
        print("=" * 60)
        print_all_properties(fact.context, "context", max_depth=max_depth - 1)

        if hasattr(fact.context, "period") and fact.context.period is not None:
            print("\n" + "=" * 60)
            print("📅 PERIOD ANALYSIS")
            print("=" * 60)
            print_all_properties(fact.context.period, "period", max_depth=max_depth - 2)
