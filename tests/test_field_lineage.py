from src.transformer.pipeline import (
    DataCleaner, Deduplicator, LineageEnricher, Normalizer, TransformationPipeline, Validator,
)


def test_field_lineage_tracks_actual_operations_and_individual_validation():
    pipeline = (TransformationPipeline().add(DataCleaner())
                .add(Normalizer(field_transforms={"price": "number", "name": "upper",
                                                 "other": "unknown"}))
                .add(Validator(["name", "missing"]))
                .add(Deduplicator()).add(LineageEnricher(execution_id="run")))
    records = [{"name": "  Ana  ", "price": "invalid", "missing": None, "other": "x",
                "_source": {"url": "https://example.com", "page_number": 2},
                "_field_meta": {"name": {"selector": ".name", "selector_type": "css",
                                            "attribute": "text"},
                                "price": {"selector": ".price", "selector_type": "css"}}}]
    result = pipeline.run(records)[0]
    assert result["name"] == "ANA"
    assert result["_lineage"]["validation_status"] == "error_tipo"
    fields = result["_field_lineage"]
    assert fields["name"]["source_url"] == result["_lineage"]["source_url"]
    assert fields["name"]["acquisition_date"] == result["_lineage"]["acquisition_date"]
    assert fields["name"]["extraction_method"] == result["_lineage"]["extraction_method"]
    assert fields["name"]["execution_id"] == "run"
    assert fields["name"]["source_field"] == "name"
    assert fields["name"]["selector"] == ".name"
    assert fields["name"]["page_number"] == 2
    assert fields["name"]["validation_status"] == "valido"
    assert fields["name"]["applied_transformations"] == ["clean", "normalize:upper"]
    assert fields["price"]["validation_status"] == "error_tipo"
    assert fields["price"]["applied_transformations"] == ["clean"]
    assert fields["missing"]["validation_status"] == "incompleto"
    assert fields["other"]["validation_status"] == "no_evaluado"
    assert fields["other"]["applied_transformations"] == ["clean"]
