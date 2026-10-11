// Microsoft.OData.Client against a view's OData endpoint (T-3510, job odata-clients-dotnet): the
// client's CSDL reader parses $metadata and its validator finds no error, then the client's
// materializer reads one Entity of the first EntitySet with $top=1. Under a restricted view, the
// model holds none of the names the view hides (the second argument, comma-separated).
using System.Xml;
using Microsoft.OData.Client;
using Microsoft.OData.Edm;
using Microsoft.OData.Edm.Csdl;
using Microsoft.OData.Edm.Validation;

if (args.Length is < 1 or > 2)
{
    Console.Error.WriteLine("usage: ODataClientCheck <service root> [hidden names]");
    return 2;
}
var root = new Uri(args[0].TrimEnd('/') + "/");
using var http = new HttpClient();
var xml = await http.GetStringAsync(new Uri(root, "$metadata"));
if (!CsdlReader.TryParse(XmlReader.Create(new StringReader(xml)), out IEdmModel model, out IEnumerable<EdmError> parse))
{
    foreach (var e in parse) Console.Error.WriteLine($"$metadata does not parse: {e}");
    return 1;
}
if (!model.Validate(out IEnumerable<EdmError> invalid))
{
    foreach (var e in invalid) Console.Error.WriteLine($"$metadata is not valid: {e}");
    return 1;
}
var hidden = args.Length == 2 ? args[1].Split(',', StringSplitOptions.RemoveEmptyEntries) : [];
var shown = model.SchemaElements.OfType<IEdmStructuredType>()
    .SelectMany(t => t.DeclaredProperties).Select(p => p.Name).Intersect(hidden).ToList();
if (shown.Count > 0)
{
    Console.Error.WriteLine($"$metadata shows hidden members: {string.Join(", ", shown)}");
    return 1;
}
var set = model.EntityContainer.EntitySets().FirstOrDefault();
if (set is null)
{
    Console.Error.WriteLine("$metadata has no EntitySet");
    return 1;
}
var context = new DataServiceContext(root, ODataProtocolVersion.V4)
{
    ResolveType = _ => typeof(Entity),
};
context.Format.UseJson(model);
var read = context.CreateQuery<Entity>(set.Name).AddQueryOption("$top", 1)
    // the check reads the key; the other members are the model's, which the validator read
    .AddQueryOption("$select", "id").Execute().ToList();
if (read.Count != 1 || string.IsNullOrEmpty(read[0].id))
{
    Console.Error.WriteLine($"{set.Name}?$top=1 read {read.Count} Entities");
    return 1;
}
Console.WriteLine($"Microsoft.OData.Client read {set.Name}('{read[0].id}')");
return 0;

[Key("id")]
public sealed class Entity
{
    public string id { get; set; } = "";
}
