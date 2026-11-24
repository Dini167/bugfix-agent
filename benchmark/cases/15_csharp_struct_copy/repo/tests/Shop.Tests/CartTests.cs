namespace Shop.Tests;

public class CartTests
{
    [Fact]
    public void Adding_the_same_product_twice_raises_its_quantity()
    {
        var cart = new Cart();
        cart.Add("apple", 2.50m);
        cart.Add("apple", 2.50m, 2);

        Assert.Single(cart.Lines);
        Assert.Equal(3, cart.Lines[0].Quantity);
    }

    [Fact]
    public void Total_counts_every_unit()
    {
        var cart = new Cart();
        cart.Add("apple", 2.50m);
        cart.Add("bread", 8.00m);
        cart.Add("apple", 2.50m);

        Assert.Equal(13.00m, cart.Total);
    }

    [Fact]
    public void Item_count_adds_up_quantities()
    {
        var cart = new Cart();
        cart.Add("apple", 2.50m);
        cart.Add("apple", 2.50m, 4);

        Assert.Equal(5, cart.ItemCount);
    }

    [Fact]
    public void Different_products_get_their_own_lines()
    {
        var cart = new Cart();
        cart.Add("apple", 2.50m);
        cart.Add("bread", 8.00m, 2);

        Assert.Equal(2, cart.Lines.Count);
        Assert.Equal(18.50m, cart.Total);
    }

    [Fact]
    public void Zero_quantity_is_rejected()
    {
        var cart = new Cart();

        Assert.Throws<ArgumentOutOfRangeException>(() => cart.Add("apple", 2.50m, 0));
    }

    [Fact]
    public void An_empty_cart_costs_nothing()
    {
        Assert.Equal(0m, new Cart().Total);
    }
}
