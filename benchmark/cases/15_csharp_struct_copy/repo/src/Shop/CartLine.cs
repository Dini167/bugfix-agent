namespace Shop;

/// <summary>One product in the cart: what it is, what one unit costs, and how many.</summary>
public struct CartLine
{
    public CartLine(string sku, decimal unitPrice, int quantity)
    {
        Sku = sku;
        UnitPrice = unitPrice;
        Quantity = quantity;
    }

    public string Sku { get; }

    public decimal UnitPrice { get; }

    public int Quantity { get; set; }

    public decimal Total => UnitPrice * Quantity;
}
