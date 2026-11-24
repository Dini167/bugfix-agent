namespace Shop;

/// <summary>A shopping cart. Adding a product that is already in it raises its quantity.</summary>
public class Cart
{
    private readonly List<CartLine> _lines = new();

    public IReadOnlyList<CartLine> Lines => _lines;

    public decimal Total => _lines.Sum(line => line.Total);

    public int ItemCount => _lines.Sum(line => line.Quantity);

    public void Add(string sku, decimal unitPrice, int quantity = 1)
    {
        if (quantity <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(quantity), "Quantity must be positive.");
        }

        for (var i = 0; i < _lines.Count; i++)
        {
            var line = _lines[i];
            if (line.Sku != sku)
            {
                continue;
            }

            line.Quantity += quantity;
            return;
        }

        _lines.Add(new CartLine(sku, unitPrice, quantity));
    }
}
