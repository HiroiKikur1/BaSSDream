using System;

namespace BassStation.Models;

public class BadgeModel
{
    public string Id { get; set; } = "";
    public string Name { get; set; } = "";
    public string Rarity { get; set; } = "silver";
    public string Description { get; set; } = "";
    public string Franchise { get; set; } = "";
    public bool IsUnlocked { get; set; }
    public bool IsEquipped { get; set; }
}
