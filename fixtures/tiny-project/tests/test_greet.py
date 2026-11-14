from greet import greet


def test_greet_says_hello_to_the_world():
    assert greet() == "Hello, world!"
